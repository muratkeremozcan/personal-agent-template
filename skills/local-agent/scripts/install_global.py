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

Expected layout of the agent repository:

    skills/local-agent/     the agent itself (must contain SKILL.md)
    skills/<companion>/     optional companion skills, linked when they contain SKILL.md
    agents/<name>.md        optional subagent definitions
"""

import argparse
import os
import sys
from pathlib import Path
from typing import NamedTuple

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


class Change(NamedTuple):
    """One planned or applied link: where it goes, its new target, and any target it replaces."""

    destination: Path
    status: str  # "installed", "repointed" or "unchanged"
    source: Path
    target: str
    previous: str | None = None


class InstallRefused(RuntimeError):
    """Raised before any link is written when one or more destinations are unsafe."""

    def __init__(self, conflicts: list[str]):
        self.conflicts = conflicts
        super().__init__("\n".join(conflicts))


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


def plan(source: Path, home: Path, force: bool = False) -> list[Change]:
    """Decide every link the install would make, or raise InstallRefused listing every conflict."""
    source = source.resolve(strict=True)
    repository = source.parent.parent
    changes: list[Change] = []
    conflicts: dict[str, None] = {}

    for directories, name, discovered in discover_sources(source):
        link_source = discovered.resolve(strict=True)
        for directory in directories:
            destination = home / directory / name
            target = os.path.relpath(link_source, start=destination.parent.resolve())

            if destination.is_symlink():
                previous = os.readlink(destination)
                current = _live_target(destination)
                if current == link_source:
                    changes.append(Change(destination, "unchanged", link_source, previous))
                    continue
                if current is not None and not _is_within(current, repository) and not force:
                    conflicts[
                        f"{destination}: links into another checkout ({previous});"
                        " rerun with --force to repoint it"
                    ] = None
                changes.append(Change(destination, "repointed", link_source, target, previous))
            elif os.path.lexists(destination):
                kind = "directory" if destination.is_dir() else "file"
                conflicts[f"{destination}: real {kind}; only symlinks are replaced"] = None
                continue
            else:
                changes.append(Change(destination, "installed", link_source, target))

            # Only destinations that will be written need a creatable, writable parent.
            for conflict in _parent_conflicts(home, destination):
                conflicts[conflict] = None

    if conflicts:
        raise InstallRefused(list(conflicts))
    return changes


def apply(changes: list[Change]) -> None:
    """Write the planned links."""
    for change in changes:
        if change.status == "unchanged":
            continue
        change.destination.parent.mkdir(parents=True, exist_ok=True)
        if change.status == "repointed":
            change.destination.unlink()
        change.destination.symlink_to(change.target, target_is_directory=change.source.is_dir())


def install(source: Path, home: Path, force: bool = False) -> list[Change]:
    """Install every skill and subagent the repository exposes into one home directory."""
    changes = plan(source, home, force)
    apply(changes)
    return changes


def describe(change: Change, dry_run: bool = False) -> str:
    """One report line for a change."""
    label = DRY_RUN_LABELS.get(change.status, change.status) if dry_run else change.status
    line = f"{label}: {change.destination} -> {change.target}"
    if change.status == "repointed":
        line += f" (was -> {change.previous})"
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

    try:
        changes = plan(AGENT_SOURCE, Path.home(), args.force)
        if not args.dry_run:
            apply(changes)
    except InstallRefused as refusal:
        print("install refused; nothing was changed:", file=sys.stderr)
        for conflict in refusal.conflicts:
            print(f"  {conflict}", file=sys.stderr)
        return EXIT_REFUSED
    except (OSError, RuntimeError) as error:
        print(f"install failed: {error}", file=sys.stderr)
        return EXIT_FAILED

    for change in changes:
        print(describe(change, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
