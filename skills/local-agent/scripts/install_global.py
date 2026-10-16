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

Writing needs an atomic no-replace rename: macOS renamex_np, or Linux renameat2 with
RENAME_NOREPLACE on a filesystem that supports it. Each filesystem the run writes to is
checked first, and one without it refuses the install with exit 2.

Writing holds an advisory lock on the home directory, so two runs cannot interleave; a
filesystem without flock runs unserialized and says so. Every removal, while installing
or rolling back, first renames the entry to a unique quarantine name without replacing
anything and inspects it there, so an entry another writer puts in place is never deleted
or overwritten. An entry or folder that changed after planning refuses the install.

The install commits once every discovery path resolves to its source. A failure before
that point undoes the run's journal; the run exits 1 and names any entry it could not
recover together with where its previous version was kept. After the commit, a
quarantined old link that cannot be deleted is a warning and the run still exits 0.

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

# Errors meaning the platform or filesystem has no atomic no-replace rename.
UNSUPPORTED_RENAME_ERRORS = {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}
RENAME_REQUIREMENT = (
    "install_global needs an atomic no-replace rename: macOS renamex_np, or Linux"
    " renameat2 with RENAME_NOREPLACE on a filesystem that supports it"
)

# flock failures that mean the filesystem cannot lock; anything else is a bug.
UNSUPPORTED_LOCK_ERRORS = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK}

# Undo steps, newest last: ("folder", path, None), ("linked", path, target), and
# ("moved", path, quarantine path).
Journal = list[tuple[str, Path, Path | str | None]]


class Folder(NamedTuple):
    """A destination's parent at planning time: where it resolves, and its nearest existing
    folder with that folder's identity."""

    resolved: Path
    anchor: Path
    device: int
    inode: int


class Change(NamedTuple):
    """One planned or applied link: where it goes, its new target, and any target it replaces."""

    destination: Path
    status: str  # "installed", "repointed" or "unchanged"
    source: Path
    target: str
    previous: str | None = None
    alias_of: "Change | None" = None  # the change that writes this path's physical entry
    parent: Folder | None = None


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
    nearest = home
    for ancestor in reversed(destination.parents):
        if not _is_within(ancestor, home):
            continue
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


def _parent_identity(destination: Path) -> Folder:
    """Creating a missing folder leaves this unchanged; retargeting a symlinked parent or
    replacing the folder changes it."""
    resolved = destination.parent.resolve()
    anchor = next(folder for folder in (resolved, *resolved.parents) if folder.is_dir())
    status = anchor.stat()
    return Folder(resolved, anchor, status.st_dev, status.st_ino)


def _parent_unchanged(change: Change) -> bool:
    anchor = change.parent.anchor
    try:
        status = anchor.stat()
        resolved = change.destination.parent.resolve()
    except (OSError, RuntimeError):
        return False
    return Folder(resolved, anchor, status.st_dev, status.st_ino) == change.parent


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


def _is_link_to(path: Path, target: str | None) -> bool:
    return path.is_symlink() and os.readlink(path) == target


def _rename_exclusive(source: Path, destination: Path) -> None:
    """Atomically rename source to destination, raising FileExistsError when it exists.

    Calls the kernel's no-replace rename: renamex_np on macOS, renameat2 on Linux.
    """
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        libc = None
    old, new = os.fsencode(source), os.fsencode(destination)
    if hasattr(libc, "renamex_np"):
        result = libc.renamex_np(old, new, ctypes.c_uint(RENAME_EXCL))
    elif hasattr(libc, "renameat2"):
        result = libc.renameat2(AT_FDCWD, old, AT_FDCWD, new, ctypes.c_uint(RENAME_NOREPLACE))
    else:
        raise OSError(errno.ENOSYS, "libc has neither renamex_np nor renameat2", str(destination))
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _require_exclusive_rename(changes: list[Change]) -> None:
    """Refuse unless each filesystem this run writes to has an atomic no-replace rename.

    Renames a scratch file once per filesystem, before anything else is written.
    """
    folders = {
        change.parent.device: change.parent.anchor
        for change in changes
        if change.status != "unchanged" and change.alias_of is None
    }
    unsupported = []
    for folder in folders.values():
        probe = folder / f".install-probe-{os.getpid()}-{secrets.token_hex(8)}"
        renamed = probe.with_name(f"{probe.name}-renamed")
        probe.touch(exist_ok=False)
        try:
            _rename_exclusive(probe, renamed)
        except OSError as error:
            if error.errno not in UNSUPPORTED_RENAME_ERRORS:
                raise
            unsupported.append(f"{folder}: {error.strerror}; {RENAME_REQUIREMENT}")
        finally:
            probe.unlink(missing_ok=True)
            renamed.unlink(missing_ok=True)
    if unsupported:
        raise InstallRefused(unsupported)


def _quarantine_name(path: Path) -> Path:
    return path.with_name(f".{path.name}.install-quarantine-{os.getpid()}-{secrets.token_hex(8)}")


def _quarantine(path: Path, holding: Path) -> bool:
    """Atomically move whatever is at path to holding; False when nothing was there.

    Every removal goes through here, so an entry is inspected only after it is out of
    the public name and nothing another writer puts at path can be deleted.
    """
    try:
        _rename_exclusive(path, holding)
    except FileNotFoundError:
        return False
    return True


def _restore(holding: Path, path: Path) -> str | None:
    """Move a quarantined entry back without replacing anything; a note if it stays put."""
    try:
        _rename_exclusive(holding, path)
    except FileExistsError:
        return f"{path}: now occupied by something else; previous entry kept at {holding}"
    except OSError as error:
        return f"{path}: could not restore ({error}); previous entry kept at {holding}"
    return None


def _roll_back(journal: Journal) -> list[str]:
    """Undo journal entries newest first, each on its own, and list what stays unrecovered."""
    leftovers = []
    for kind, path, detail in reversed(journal):
        try:
            if kind == "folder":
                with contextlib.suppress(FileNotFoundError):
                    path.rmdir()
            elif kind == "linked":
                holding = _quarantine_name(path)
                if not _quarantine(path, holding):
                    continue
                if not _is_link_to(holding, detail):
                    if note := _restore(holding, path):
                        leftovers.append(note)
                    continue
                try:
                    holding.unlink()
                except OSError as error:
                    leftovers.append(
                        f"{holding}: could not delete this run's link from {path} ({error})"
                    )
            elif os.path.lexists(detail) and (note := _restore(detail, path)):
                leftovers.append(note)
        except OSError as error:
            if kind != "folder" or error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                leftovers.append(f"{path}: could not undo this run's {kind} entry ({error})")
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


def _write(change: Change, journal: Journal) -> None:
    """Make one planned link, journaling each step before it happens."""
    destination = change.destination
    missing = [parent for parent in destination.parents if not os.path.lexists(parent)]
    for folder in reversed(missing):
        with contextlib.suppress(FileExistsError):
            folder.mkdir()
            journal.append(("folder", folder, None))

    if change.status == "repointed":
        holding = _quarantine_name(destination)
        journal.append(("moved", destination, holding))
        if not _quarantine(destination, holding):
            raise InstallRefused([f"{destination}: removed after planning; rerun the install"])
        if not _is_link_to(holding, change.previous):
            raise InstallRefused([f"{destination}: changed after planning; left as it is"])

    journal.append(("linked", destination, change.target))
    try:
        destination.symlink_to(change.target, target_is_directory=change.source.is_dir())
    except FileExistsError:
        raise InstallRefused([f"{destination}: created by something else after planning"]) from None


def apply(changes: list[Change]) -> list[str]:
    """Write the planned links as one transaction and return post-commit warnings.

    Every logical destination's folder must still be the physical folder it was at
    planning time. A replaced link is quarantined and checked against the plan before
    its successor is created exclusively. The install commits once every logical
    destination resolves to its source. A failure before that point rolls the journal
    back and raises InstallIncomplete for anything it could not recover. Deleting the
    quarantined old links happens after the commit, so a failure there is a warning.
    """
    stale = [
        f"{change.destination}: its folder changed after planning; rerun the install"
        for change in changes
        if not _parent_unchanged(change)
    ]
    if stale:
        raise InstallRefused(stale)
    _require_exclusive_rename(changes)

    journal: Journal = []
    try:
        for change in changes:
            if change.status != "unchanged" and change.alias_of is None:
                _write(change, journal)
        _verify(changes)
    except BaseException as error:
        leftovers = _roll_back(journal)
        if leftovers:
            raise InstallIncomplete(error, leftovers) from error
        raise

    warnings = []
    for kind, _, holding in journal:
        if kind == "moved":
            try:
                holding.unlink()
            except OSError as error:
                warnings.append(f"{holding}: backup of a replaced link left behind ({error})")
    return warnings


@contextlib.contextmanager
def install_lock(home: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on the home directory for one install run.

    Locking the directory itself leaves nothing behind. Without fcntl, or on a filesystem
    that does not support flock, the run continues unserialized and says so on stderr.
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
            if error.errno not in UNSUPPORTED_LOCK_ERRORS:
                raise
            print(
                f"warning: cannot lock {home} ({error}); this run is not serialized"
                " against other installs",
                file=sys.stderr,
            )
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
