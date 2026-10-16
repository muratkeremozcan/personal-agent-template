#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Link the agent, its companion skills, and its subagents into each tool's discovery folders.

Usage:
    uv run skills/local-agent/scripts/install_global.py [--dry-run] [--force]

One checkout of the agent repository then serves every AI tool on the machine that reads
user-level skills, and an edit in the repository is live everywhere at once. The script
only ever creates or repairs relative symlinks.

Every destination is checked before the first link is written. The install refuses as a
whole, exits 2, and leaves the machine exactly as it was when any destination:

- is a real file or directory, so an installer-managed copy is never overwritten;
- sits under a path that exists as a file where a directory is needed;
- sits in a directory the current user cannot write;
- is a symlink into a different checkout, unless --force is given.

A symlink into this checkout, or one whose target no longer exists, is repointed and
reported with its previous target. --dry-run prints the same report and touches nothing.

Discovery folders that share one physical folder, such as ~/.agents symlinked to
~/.claude, get one link, and every path that reaches it is reported.

Writing holds an advisory lock on the home directory, so two runs cannot interleave.
An entry or folder that changed after planning is left as it is and refuses the install.
The install commits once every discovery path resolves to its source. A failure before
that point removes the links this run created and restores each replaced link without
overwriting anything that appeared meanwhile; the run exits 1 and names any entry it
could not recover together with where its previous version was kept. After the commit,
a backup that cannot be deleted is reported as a warning and the run still exits 0.

Expected layout of the agent repository:

    skills/local-agent/     the agent itself (must contain SKILL.md)
    skills/<companion>/     optional companion skills, linked when they contain SKILL.md
    agents/<name>.md        optional subagent definitions
"""

import argparse
import contextlib
import ctypes
import errno
import os
import secrets
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sanctum import SKILL_NAME

# Discovery folders under the user's home, one per tool that reads user-level skills.
SKILL_DIRECTORIES = (
    Path(".claude/skills"),
    Path(".agents/skills"),
    Path(".codex/skills"),
    Path(".gemini/config/skills"),
)

AGENT_DIRECTORIES = (Path(".claude/agents"),)

# The agent skill directory this copy of the script belongs to.
AGENT_SOURCE = Path(__file__).resolve().parent.parent

EXIT_FAILED = 1
EXIT_REFUSED = 2

DRY_RUN_LABELS = {"installed": "would install", "repointed": "would repoint"}

# No-replace rename flags: renamex_np on macOS, renameat2 on Linux.
RENAME_EXCL = 0x4
RENAME_NOREPLACE = 0x1
AT_FDCWD = -100


class Change(NamedTuple):
    """One planned or applied link: where it goes, its new target, and any target it replaces."""

    destination: Path
    status: str  # "installed", "repointed" or "unchanged"
    source: Path
    target: str
    previous: str | None = None
    alias_of: "Change | None" = None  # the change that writes this path's physical entry
    parent: tuple | None = None  # the parent folder's identity at planning time


class InstallRefused(RuntimeError):
    """The install was refused and the machine is as it was before the run."""

    def __init__(self, conflicts: list[str]):
        self.conflicts = conflicts
        super().__init__("\n".join(conflicts))


class InstallIncomplete(RuntimeError):
    """A write failed and rollback could not recover every entry."""

    def __init__(self, error: BaseException, leftovers: list[str]):
        self.error = error
        self.leftovers = leftovers
        super().__init__("\n".join([str(error), *leftovers]))


def discover_sources(source: Path) -> list[tuple[tuple[Path, ...], str, Path]]:
    """Every link this repository exposes, as (discovery roots, link name, source path)."""
    if not (source / "SKILL.md").is_file():
        raise RuntimeError(
            f"SKILL.md is missing from {source}. Agent Builder writes it during setup."
        )

    sources = [(SKILL_DIRECTORIES, SKILL_NAME, source)]

    for child in sorted(source.parent.iterdir()):
        if child.resolve() != source.resolve() and (child / "SKILL.md").is_file():
            sources.append((SKILL_DIRECTORIES, child.name, child))

    agent_root = source.parent.parent / "agents"
    if agent_root.is_dir():
        for child in sorted(agent_root.glob("*.md")):
            sources.append((AGENT_DIRECTORIES, child.name, child))

    return sources


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _live_target(link: Path) -> Path | None:
    """Where a symlink resolves, or None when its target is gone or it loops."""
    try:
        return link.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _parent_conflicts(home: Path, destination: Path) -> list[str]:
    """Problems that would stop the destination's parent directories from being created."""
    ancestors = [home]
    for part in destination.parent.relative_to(home).parts:
        ancestors.append(ancestors[-1] / part)

    nearest = home
    for ancestor in ancestors:
        if not os.path.lexists(ancestor):
            break
        if not ancestor.is_dir():
            kind = "broken symlink" if ancestor.is_symlink() else "file"
            return [f"{ancestor}: {kind} where a directory is needed"]
        nearest = ancestor

    if not os.access(nearest, os.W_OK | os.X_OK):
        return [f"{nearest}: directory is not writable"]
    return []


def _case_insensitive(directory: Path) -> bool:
    """Whether names under this directory fold case, probed without writing anything.

    Looks up one existing entry under its swapped-case spelling, walking up the tree
    while it stays on the same filesystem.
    """
    device = directory.stat().st_dev
    probe = directory
    while True:
        with os.scandir(probe) as entries:
            name = next((e.name for e in entries if e.name.swapcase() != e.name), None)
        if name is not None:
            try:
                return os.path.samestat(
                    os.lstat(probe / name), os.lstat(probe / name.swapcase())
                )
            except FileNotFoundError:
                return False
        if probe.parent == probe or probe.parent.stat().st_dev != device:
            return False
        probe = probe.parent


def _physical_key(destination: Path) -> tuple[int, int, tuple[str, ...]]:
    """Identity of the directory entry a destination names.

    Two discovery paths that reach one folder through a symlinked or case-aliased parent
    share a key: the inode of their deepest existing directory plus the names below it.
    """
    existing = destination.parent
    tail = [destination.name]
    while not existing.is_dir():
        tail.insert(0, existing.name)
        existing = existing.parent
    if _case_insensitive(existing):
        tail = [part.casefold() for part in tail]
    status = existing.stat()
    return status.st_dev, status.st_ino, tuple(tail)


def _parent_identity(destination: Path) -> tuple[Path, Path, int, int]:
    """Where a destination's parent resolves, and the inode of its nearest existing folder.

    Creating a missing folder leaves this unchanged; retargeting a symlinked parent or
    replacing the folder changes it.
    """
    parent = destination.parent.resolve()
    anchor = parent
    while not anchor.is_dir():
        anchor = anchor.parent
    status = anchor.stat()
    return parent, anchor, status.st_dev, status.st_ino


def _parent_unchanged(change: Change) -> bool:
    parent, anchor, device, inode = change.parent
    try:
        status = anchor.stat()
        resolved = change.destination.parent.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == parent and (status.st_dev, status.st_ino) == (device, inode)


def plan(source: Path, home: Path, force: bool = False) -> list[Change]:
    """Decide every link the install would make, or raise InstallRefused listing every conflict."""
    source = source.resolve(strict=True)
    repository = source.parent.parent
    changes: list[Change] = []
    conflicts: dict[str, None] = {}
    claimed: dict[tuple[int, int, tuple[str, ...]], Change] = {}

    for directories, name, discovered in discover_sources(source):
        link_source = discovered.resolve(strict=True)
        for directory in directories:
            destination = home / directory / name
            key = _physical_key(destination)
            parent = _parent_identity(destination)

            primary = claimed.get(key)
            if primary is not None:
                if primary.source != link_source:
                    conflicts[
                        f"{destination}: same path as {primary.destination},"
                        f" which links to {primary.source}; cannot also link to {link_source}"
                    ] = None
                else:
                    changes.append(
                        primary._replace(
                            destination=destination, alias_of=primary, parent=parent
                        )
                    )
                continue

            target = os.path.relpath(link_source, start=destination.parent.resolve())

            if destination.is_symlink():
                previous = os.readlink(destination)
                current = _live_target(destination)
                if current == link_source:
                    change = Change(destination, "unchanged", link_source, previous, parent=parent)
                    claimed[key] = change
                    changes.append(change)
                    continue
                if current is not None and not _is_within(current, repository) and not force:
                    conflicts[
                        f"{destination}: links into another checkout ({previous});"
                        " rerun with --force to repoint it"
                    ] = None
                change = Change(
                    destination, "repointed", link_source, target, previous, parent=parent
                )
            elif os.path.lexists(destination):
                kind = "directory" if destination.is_dir() else "file"
                conflicts[f"{destination}: real {kind}; only symlinks are replaced"] = None
                continue
            else:
                change = Change(destination, "installed", link_source, target, parent=parent)
            claimed[key] = change
            changes.append(change)

            # Only destinations that will be written need a creatable, writable parent.
            for conflict in _parent_conflicts(home, destination):
                conflicts[conflict] = None

    if conflicts:
        raise InstallRefused(list(conflicts))
    return changes


def _make_parents(directory: Path) -> list[Path]:
    """Create a directory and its missing parents, returning the ones created, deepest last."""
    missing = []
    probe = directory
    while not os.path.lexists(probe):
        missing.append(probe)
        probe = probe.parent
    directory.mkdir(parents=True, exist_ok=True)
    return missing[::-1]


def _is_link_to(path: Path, target: str | None) -> bool:
    return path.is_symlink() and os.readlink(path) == target


def _rename_exclusive(source: Path, destination: Path) -> None:
    """Rename source to destination, raising FileExistsError when destination exists.

    Uses the kernel's no-replace rename (renamex_np on macOS, renameat2 on Linux). A
    platform without one falls back to a hard link for anything that is not a directory.
    """
    old, new = os.fsencode(source), os.fsencode(destination)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        libc = None

    code = errno.ENOSYS
    if libc is not None and hasattr(libc, "renamex_np"):
        if libc.renamex_np(old, new, ctypes.c_uint(RENAME_EXCL)) == 0:
            return
        code = ctypes.get_errno()
    elif libc is not None and hasattr(libc, "renameat2"):
        if libc.renameat2(AT_FDCWD, old, AT_FDCWD, new, ctypes.c_uint(RENAME_NOREPLACE)) == 0:
            return
        code = ctypes.get_errno()

    if code in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP) and not source.is_dir():
        os.link(source, destination, follow_symlinks=False)
        source.unlink()
        return
    raise OSError(code, os.strerror(code), str(destination))


def _restore(backup: Path, destination: Path) -> str | None:
    """Return a set-aside entry to its name, never replacing whatever is there now.

    A symlink is recreated from its target, which fails if anything holds the name, and
    the backup is removed only after that succeeds. Returns a note when the entry is back
    in place but its backup could not be removed.
    """
    if not backup.is_symlink():
        _rename_exclusive(backup, destination)
        return None
    os.symlink(os.readlink(backup), destination)
    try:
        backup.unlink()
    except OSError as error:
        return f"{backup}: {destination} was restored, but this backup remains ({error})"
    return None


def _set_aside(change: Change) -> Path:
    """Move the planned symlink to a backup name, refusing if it changed after planning."""
    destination = change.destination
    changed = f"{destination}: changed after planning; left as it is"
    removed = f"{destination}: removed after planning; rerun the install"
    if not os.path.lexists(destination):
        raise InstallRefused([removed])
    if not _is_link_to(destination, change.previous):
        raise InstallRefused([changed])

    backup = destination.with_name(
        f".{destination.name}.install-backup-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        os.rename(destination, backup)
    except FileNotFoundError:
        raise InstallRefused([removed]) from None
    if not _is_link_to(backup, change.previous):
        # Another actor replaced the link between the check and the rename.
        try:
            note = _restore(backup, destination)
        except OSError as error:
            note = f"{destination}: the entry found there was kept at {backup} ({error})"
        if note:
            raise InstallIncomplete(InstallRefused([changed]), [note])
        raise InstallRefused([changed])
    return backup


def _roll_back(written: list[tuple[Change, Path | None]], created: list[Path]) -> list[str]:
    """Undo this run's links, restore replaced ones, and remove directories it created.

    Every entry gets its own recovery attempt; whatever cannot be recovered is returned
    with the location of the entry it would have restored.
    """
    leftovers = []
    for change, backup in reversed(written):
        destination = change.destination
        kept = f"; previous entry kept at {backup}" if backup else ""
        try:
            if _is_link_to(destination, change.target):
                destination.unlink()
        except OSError as error:
            leftovers.append(f"{destination}: could not remove the new link ({error}){kept}")
            continue
        if backup is None:
            continue
        try:
            note = _restore(backup, destination)
            if note:
                leftovers.append(note)
        except FileExistsError:
            leftovers.append(f"{destination}: now occupied by something else{kept}")
        except OSError as error:
            leftovers.append(f"{destination}: could not restore ({error}){kept}")
    for directory in reversed(created):
        try:
            directory.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                leftovers.append(f"{directory}: could not remove this run's folder ({error})")
    return leftovers


def _verify(changes: list[Change]) -> None:
    """Refuse unless every logical destination now resolves to its source."""
    unreachable = [
        f"{change.destination}: does not reach {change.source} after writing;"
        " a folder changed during the install"
        for change in changes
        if _live_target(change.destination) != change.source
    ]
    if unreachable:
        raise InstallRefused(unreachable)


def apply(changes: list[Change]) -> list[str]:
    """Write the planned links as one transaction and return post-commit warnings.

    Before writing, every logical destination's folder must still be the physical folder
    it was at planning time. A replaced link is checked against the plan, moved to a
    backup name, and checked again before its successor is created; a new link never
    overwrites an entry that appeared after planning.

    The install commits once every logical destination resolves to its source. A failure
    before that point undoes this run's writes and restores the replaced links, and
    raises InstallIncomplete listing anything that could not be recovered. Removing the
    backups happens after the commit, so a failure there is returned as a warning.
    """
    stale = [
        f"{change.destination}: its folder changed after planning; rerun the install"
        for change in changes
        if not _parent_unchanged(change)
    ]
    if stale:
        raise InstallRefused(stale)

    written: list[tuple[Change, Path | None]] = []
    created: list[Path] = []
    try:
        for change in changes:
            if change.status == "unchanged" or change.alias_of is not None:
                continue
            created.extend(_make_parents(change.destination.parent))
            backup = _set_aside(change) if change.status == "repointed" else None
            written.append((change, backup))
            try:
                change.destination.symlink_to(
                    change.target, target_is_directory=change.source.is_dir()
                )
            except FileExistsError:
                raise InstallRefused(
                    [f"{change.destination}: created by something else after planning"]
                ) from None
        _verify(changes)
    except BaseException as error:
        original, leftovers = error, []
        if isinstance(error, InstallIncomplete):
            original, leftovers = error.error, list(error.leftovers)
        leftovers += _roll_back(written, created)
        if leftovers:
            raise InstallIncomplete(original, leftovers) from original
        if original is not error:
            raise original from None
        raise

    warnings = []
    for _, backup in written:
        if backup is None:
            continue
        try:
            backup.unlink()
        except OSError as error:
            warnings.append(f"{backup}: backup of a replaced link left behind ({error})")
    return warnings


@contextlib.contextmanager
def install_lock(home: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on the home directory for one install run.

    Locking the directory itself leaves nothing behind. Platforms without fcntl, and
    filesystems that do not support flock, run unlocked.
    """
    if fcntl is None:
        yield
        return
    descriptor = os.open(home, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            message = f"{home}: another install_global run holds the install lock"
            raise InstallRefused([message]) from None
        except OSError as error:
            if error.errno not in (errno.ENOLCK, errno.ENOTSUP, errno.EBADF, errno.EINVAL):
                raise
        yield
    finally:
        os.close(descriptor)


def install(source: Path, home: Path, force: bool = False) -> list[Change]:
    """Install every skill and subagent the repository exposes into one home directory.

    Post-commit warnings are dropped; main() reports them.
    """
    with install_lock(home):
        changes = plan(source, home, force)
        apply(changes)
    return changes


def describe(change: Change, dry_run: bool = False) -> str:
    """One report line for a change."""
    label = DRY_RUN_LABELS.get(change.status, change.status) if dry_run else change.status
    line = f"{label}: {change.destination} -> {change.target}"
    if change.status == "repointed":
        line += f" (was -> {change.previous})"
    if change.alias_of is not None:
        line += f" (same path as {change.alias_of.destination})"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Link the agent, its companion skills, and its subagents into every AI tool's"
            " user-level discovery folders under your home directory."
        ),
        epilog=(
            f"Exit status: 0 on success, {EXIT_REFUSED} when a conflict refuses the whole"
            f" install, {EXIT_FAILED} on any other failure."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would change and touch nothing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="repoint symlinks that currently link into a different checkout",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    warnings: list[str] = []
    try:
        if args.dry_run:
            changes = plan(AGENT_SOURCE, home, args.force)
        else:
            with install_lock(home):
                changes = plan(AGENT_SOURCE, home, args.force)
                warnings = apply(changes)
    except InstallRefused as refusal:
        print("install refused; nothing was changed:", file=sys.stderr)
        for conflict in refusal.conflicts:
            print(f"  {conflict}", file=sys.stderr)
        return EXIT_REFUSED
    except InstallIncomplete as failure:
        print(f"install failed: {failure.error}", file=sys.stderr)
        print("rollback could not undo everything:", file=sys.stderr)
        for leftover in failure.leftovers:
            print(f"  {leftover}", file=sys.stderr)
        return EXIT_FAILED
    except (OSError, RuntimeError) as error:
        print(f"install failed: {error}", file=sys.stderr)
        return EXIT_FAILED

    for change in changes:
        print(describe(change, args.dry_run))
    for warning in warnings:
        print(f"warning: {warning}; delete it by hand", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
