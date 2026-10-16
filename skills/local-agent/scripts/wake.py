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

Modes:
  FIRST_BREATH        no sanctum at the canonical home yet -> the one birth.
  FIRST_BREATH_RESUME sanctum scaffolded but the ".born" marker is absent, so a
                      prior First Breath was interrupted before it finished. The
                      files still hold seed placeholders; resume the birth rather
                      than waking as if fully formed.
  WAKING              sanctum present and sealed with ".born" -> the normal path.
  DAMAGED             a sanctum exists but identity files are missing from it, as
                      a sync conflict or partial checkout leaves it. Restore them;
                      a birth here would overwrite a real identity. Exits 1.
  HOME_MISCONFIGURED  LOCAL_AGENT_HOME is relative or names a directory that does
                      not exist, which is a typo far more often than a new home.
                      Fix the variable before anything else. Exits 1.

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

import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sanctum import (
    BORN_MARKER,
    IDENTITY_FILES,
    SKILL_NAME,
    HomeMisconfigured,
    birth_date,
    health,
    missing_identity_files,
    read_text,
    sanctum_home,
)

# Printed as an absolute path because the owner, or the agent, runs it from whatever
# directory the session opened in. A relative `scripts/curate.py` resolves only from
# inside the skill bundle, so the command in the notice failed as printed.
CURATE_SCRIPT = Path(__file__).resolve().parent / "curate.py"


def emit(path: Path) -> None:
    print(f"\n===== {path.name} =====")
    try:
        print(read_text(path).rstrip())
    except FileNotFoundError:
        print(f"(missing: {path.name})")


def emit_curation_notice(sanctum: Path, project_root: Path) -> None:
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
        "Exact token counts: uv run "
        f"{shlex.quote(str(CURATE_SCRIPT))} {shlex.quote(str(project_root))}"
    )


def emit_damage_report(home: Path, sanctum: Path, missing: list[str], project_root: Path) -> None:
    """Name what is missing and how to get it back, and hold the birth off.

    This state once read as "no sanctum", so waking announced the one birth over a
    real identity. first-breath.md then rewrote BOND.md and PERSONA.md and stamped a
    new date into `.born`, which also cost the original First Breath log its
    exemption. A sanctum that exists is never a birth.
    """
    born = (sanctum / BORN_MARKER).is_file()
    born_on = birth_date(sanctum)
    relative = [f"{sanctum.relative_to(home).as_posix()}/{name}" for name in missing]
    quoted = " ".join(shlex.quote(r) for r in relative)
    git = f"git -C {shlex.quote(str(home))}"

    print("MODE: DAMAGED")
    print(f"Invoked from: {project_root}")
    print(f"Sanctum: {sanctum}")
    print(f"Missing identity files: {', '.join(missing)}")
    if born:
        print(f"{BORN_MARKER}: present ({born_on.isoformat() if born_on else 'no date'})")
    else:
        print(f"{BORN_MARKER}: absent")
    print(
        "\nThis sanctum holds an identity, so this is a restore. Do not run First Breath "
        f"or init-sanctum.py, do not write {BORN_MARKER}, and do not rewrite the files "
        "that are still present. Tell your owner which files are missing and stop until "
        "they are back.\n"
        "\nRestore them from history or a backup:\n"
        f"  {git} status -- {quoted}\n"
        f"  {git} checkout HEAD -- {quoted}    # deleted since the last commit\n"
        f"  {git} log --oneline -- {quoted}    # find an older version to check out\n"
        "or copy them back from a backup or another synced device."
    )
    if not born:
        print(
            "If this folder is only a scaffold left by an interrupted init-sanctum.py, with "
            "no conversation saved in it, move it aside and run First Breath again."
        )


def main() -> int:
    # Identity files are printed verbatim, U+FFFD from a bad byte included. A pipe on
    # a legacy code page cannot encode every character, and waking must not crash
    # over one.
    sys.stdout.reconfigure(errors="replace")
    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print("Usage: wake.py <project-root>", file=sys.stderr)
        return 2

    project_root = Path(positional[0]).resolve()
    try:
        home = sanctum_home()
    except HomeMisconfigured as exc:
        print("MODE: HOME_MISCONFIGURED")
        print(f"Invoked from: {project_root}")
        print(str(exc))
        print(
            "Do not start First Breath and do not run init-sanctum.py: with the home "
            "unresolved, a birth would create a second identity in the wrong place. "
            "Tell your owner the exact value above and stop until it is fixed."
        )
        return 1
    sanctum = home / "_bmad" / "memory" / SKILL_NAME

    # lexists, so a dangling symlink at the sanctum path reads as damaged.
    if not os.path.lexists(sanctum):
        print("MODE: FIRST_BREATH")
        print(f"Invoked from: {project_root}")
        print(f"NO SANCTUM at {sanctum}")
        print("This is your one birth. Load references/first-breath.md and follow it.")
        return 0

    missing = missing_identity_files(sanctum)
    if missing:
        emit_damage_report(home, sanctum, missing, project_root)
        return 1

    if not (sanctum / BORN_MARKER).is_file():
        print("MODE: FIRST_BREATH_RESUME")
        print(f"Invoked from: {project_root}")
        print(
            f"Sanctum scaffolded at {sanctum}, but First Breath never finished "
            f"(no {BORN_MARKER} marker)."
        )
        print(
            "You are half-born: the files exist but still hold seed placeholders, "
            "not a real self yet. Do NOT greet as though fully formed, and do not claim "
            "a name, mission, or memory the files do not actually contain. Load "
            "references/first-breath.md and continue the birth from where it left off, "
            "building on whatever is already saved."
        )
        return 0

    print("MODE: WAKING")
    print(f"Invoked from: {project_root}")
    print(f"Sanctum: {sanctum}")
    for name in IDENTITY_FILES:
        emit(sanctum / name)
    emit_curation_notice(sanctum, project_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
