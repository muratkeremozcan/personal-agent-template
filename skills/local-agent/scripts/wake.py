#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
Waking: load the agent's sanctum in one pass, or route to First Breath.

Run on activation. Determines the mode from the filesystem and, when the
sanctum exists and First Breath finished, prints the full identity in a single
read (INDEX, PERSONA, CREED, BOND, MEMORY, CAPABILITIES) so the agent becomes
itself in one shot instead of six.

Three modes:
  FIRST_BREATH        no sanctum scaffolded yet -> the one birth.
  FIRST_BREATH_RESUME sanctum scaffolded but the ".born" marker is absent, so a
                      prior First Breath was interrupted before it finished. The
                      files still hold seed placeholders; resume the birth rather
                      than waking as if fully formed.
  WAKING              sanctum present and sealed with ".born" -> the normal path.

This loads runtime memory only. It never reads or writes config or customize.toml.

The sanctum lives at one fixed canonical home, independent of the invocation
directory. This prevents an unrelated project from triggering a false First
Breath or creating a second identity.

Usage:
    uv run wake.py <project-root>

    project-root: the project you're actually working in this session.
                  Informational only; printed back for situational
                  awareness, but it never determines where the sanctum lives.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sanctum import (  # noqa: E402
    BORN_MARKER,
    IDENTITY_FILES,
    SKILL_NAME,
    health,
    sanctum_home,
)


def emit(path: Path) -> None:
    print(f"\n===== {path.name} =====")
    try:
        print(path.read_text(encoding="utf-8").rstrip())
    except FileNotFoundError:
        print(f"(missing: {path.name})")


def emit_curation_notice(sanctum: Path) -> None:
    """Fire the curation pass from waking itself, so nothing has to schedule it.

    A soft token guardrail that only runs when someone remembers to ask for it
    is not a guardrail. Waking already runs on every activation, so the check
    belongs here: no cron, no launchd, no hook, nothing to reinstall on a second
    machine, and nothing that can silently stop firing.

    Byte-based and stdlib-only to keep waking fast. curate.py gives exact counts.
    """
    report = health(sanctum)
    if not report["curation_due"]:
        return

    print("\n===== CURATION DUE =====")
    for reason in report["reasons"]:
        print(f"- {reason}")
    print(
        "\nRun the curation pass before this session ends: load "
        "`references/curation-pass.md` and follow it.\n"
        "Do not derail what your owner asked for. If the session is a one-shot "
        "Remember or Recall, or they are mid-thread, do the pass after their "
        "work is done, and never announce it as a status report.\n"
        f"Exact token counts: uv run scripts/curate.py {sanctum.parent.parent.parent}"
    )


def main() -> int:
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("Usage: wake.py <project-root>", file=sys.stderr)
        return 2

    project_root = Path(positional[0]).resolve()
    sanctum = sanctum_home() / "_bmad" / "memory" / SKILL_NAME

    scaffolded = (
        sanctum.is_dir()
        and (sanctum / "CREED.md").is_file()
        and (sanctum / "MEMORY.md").is_file()
    )
    if not scaffolded:
        print("MODE: FIRST_BREATH")
        print(f"Invoked from: {project_root}")
        print(f"NO SANCTUM at {sanctum}")
        print("This is your one birth. Load references/first-breath.md and follow it.")
        return 0

    if not (sanctum / BORN_MARKER).is_file():
        print("MODE: FIRST_BREATH_RESUME")
        print(f"Invoked from: {project_root}")
        print(
            f"Sanctum scaffolded at {sanctum}, but First Breath never finished (no {BORN_MARKER} marker)."
        )
        print(
            "You are half-born: the files exist but still hold seed placeholders, not a real self yet. "
            "Do NOT greet as though fully formed, and do not claim a name, mission, or memory the files do "
            "not actually contain. Load references/first-breath.md and continue the birth from where it "
            "left off, building on whatever is already saved."
        )
        return 0

    print("MODE: WAKING")
    print(f"Invoked from: {project_root}")
    print(f"Sanctum: {sanctum}")
    for name in IDENTITY_FILES:
        emit(sanctum / name)
    emit_curation_notice(sanctum)
    return 0


if __name__ == "__main__":
    sys.exit(main())
