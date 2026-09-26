#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Link the agent, its companion skills, and its subagents into each tool's discovery folders.

Usage:
    uv run skills/local-agent/scripts/install_global.py

One checkout of the agent repository then serves every AI tool on the machine that reads
user-level skills, and an edit in the repository is live everywhere at once. The script
only ever creates or repairs relative symlinks; it refuses to replace a real file or
directory, so an installer-managed copy is never overwritten.

Expected layout of the agent repository:

    skills/local-agent/     the agent itself (must contain SKILL.md)
    skills/<companion>/     optional companion skills, linked when they contain SKILL.md
    agents/<name>.md        optional subagent definitions
"""

import os
import sys
from pathlib import Path

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


def install_link(source: Path, destination: Path) -> str:
    """Create or repair one relative symlink."""
    source = source.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.is_symlink():
        if destination.resolve(strict=False) == source:
            return "unchanged"
        destination.unlink()
    elif destination.exists():
        raise RuntimeError(f"Refusing to replace non-symlink path: {destination}")

    target = os.path.relpath(source, start=destination.parent.resolve())
    destination.symlink_to(target, target_is_directory=source.is_dir())
    return "installed"


def install(source: Path, home: Path) -> list[tuple[Path, str]]:
    """Install every skill and subagent the repository exposes into one home directory.

    All conflicts are checked before any link is written, so a refusal leaves the
    machine exactly as it was.
    """
    planned = [
        (link_source, home / directory / name)
        for directories, name, link_source in discover_sources(source)
        for directory in directories
    ]

    conflicts = [
        destination
        for _, destination in planned
        if destination.exists() and not destination.is_symlink()
    ]
    if conflicts:
        paths = ", ".join(str(path) for path in conflicts)
        raise RuntimeError(f"Refusing to replace non-symlink path: {paths}")

    return [
        (destination, install_link(link_source, destination))
        for link_source, destination in planned
    ]


def main() -> int:
    source = Path(__file__).resolve().parent.parent
    try:
        results = install(source, Path.home())
    except (OSError, RuntimeError) as error:
        print(f"install failed: {error}", file=sys.stderr)
        return 1

    for destination, status in results:
        print(f"{status}: {destination} -> {os.readlink(destination)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
