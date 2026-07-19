#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["tiktoken"]
# ///
"""curate: report-only sanctum-hygiene metrics for the curation pass.

The agent decides what to distill, merge, prune, and delete; that judgment stays
in the prompt. This script only measures the things a model cannot eyeball
reliably, and prints them as one JSON object so the curation prompt can reason
over exact numbers instead of estimates:

  - the exact token count of MEMORY.md, and whether it is over its guardrail
  - which session logs have aged past the retention threshold (by the date in
    the filename), so the aged ones can be pruned
  - which files on disk have drifted out of INDEX.md ("an unlisted file is a
    lost file")

It reads the sanctum only; it never edits, prunes, or writes anything, and it
never touches config or customize.toml.

The sanctum lives at one fixed canonical home, independent of the invocation
directory. This is the same location rule used by wake.py.

Usage:
    uv run curate.py <project-root> [--days N] [--guardrail N]

    project-root: the project you're actually working in this session.
                  Informational only; it never determines sanctum location.
    --days:       session-log retention threshold in days (default 14)
    --guardrail:  MEMORY.md soft token guardrail (default 1500)

Exit codes: 0 success, 1 no sanctum found, 2 usage error.
"""

import argparse
import importlib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

SKILL_NAME = "local-agent"
BORN_MARKER = ".born"


def sanctum_home() -> Path:
    """Canonical sanctum home: $LOCAL_AGENT_HOME if set, else ~/local-agent.

    The invocation directory never changes the sanctum location. Mirrors
    wake.py's sanctum_home().
    """
    override = os.environ.get("LOCAL_AGENT_HOME")
    home = Path(override) if override else Path.home() / "local-agent"
    return home.expanduser().resolve()


# The always-present skeleton: structural, not organic, so not index-drift candidates.
SKELETON = {
    "INDEX.md",
    "PERSONA.md",
    "CREED.md",
    "BOND.md",
    "MEMORY.md",
    "CAPABILITIES.md",
}
# Structural directories that are never individually indexed as organic files.
STRUCTURAL_DIRS = {"references", "scripts", "sessions"}

DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def count_tokens(text: str) -> tuple[int, str]:
    """Return (token_count, method), falling back to chars//4 without tiktoken."""
    try:
        tiktoken = importlib.import_module("tiktoken")
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), "tiktoken"
    except Exception:
        return len(text) // 4, "fallback"


def measure_memory(sanctum: Path, guardrail: int) -> dict:
    memory = sanctum / "MEMORY.md"
    if not memory.is_file():
        return {
            "tokens": 0,
            "method": "missing",
            "guardrail": guardrail,
            "over_guardrail": False,
        }
    tokens, method = count_tokens(memory.read_text(encoding="utf-8"))
    return {
        "tokens": tokens,
        "method": method,
        "guardrail": guardrail,
        "over_guardrail": tokens > guardrail,
    }


def stale_session_logs(sanctum: Path, days: int, today: date) -> dict:
    sessions = sanctum / "sessions"
    logs = sorted(p.name for p in sessions.glob("*.md")) if sessions.is_dir() else []
    stale = []
    for name in logs:
        m = DATE_IN_NAME.search(name)
        if not m:
            continue
        try:
            log_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if (today - log_date).days > days:
            stale.append(name)
    return {"total": len(logs), "stale": stale, "days_threshold": days}


def index_drift(sanctum: Path) -> dict:
    """Files on disk whose basename never appears in INDEX.md."""
    index = sanctum / "INDEX.md"
    index_text = index.read_text(encoding="utf-8") if index.is_file() else ""

    candidates: list[str] = []
    # Organic entries at the sanctum root.
    for entry in sorted(sanctum.iterdir()):
        if entry.name.startswith(".") or entry.name in SKELETON:
            continue
        if entry.is_dir() and entry.name in STRUCTURAL_DIRS:
            continue
        if entry.name == "capabilities":
            # Learned capabilities must each be registered.
            for cap in sorted(entry.iterdir()):
                candidates.append(f"capabilities/{cap.name}")
            continue
        if entry.name == "knowledge":
            # Every imported knowledge file must remain individually discoverable.
            for knowledge_file in sorted(entry.rglob("*")):
                relative = knowledge_file.relative_to(sanctum)
                if knowledge_file.is_file() and not any(
                    part.startswith(".") for part in relative.parts
                ):
                    candidates.append(relative.as_posix())
            continue
        candidates.append(entry.name + ("/" if entry.is_dir() else ""))

    unlisted = [c for c in candidates if Path(c).name not in index_text]
    return {"unlisted": unlisted, "has_index": index.is_file()}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "project_root",
        help="the project you're working in (informational only; it does not determine sanctum location)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=14,
        help="session-log retention threshold (default 14)",
    )
    p.add_argument(
        "--guardrail",
        type=int,
        default=1500,
        help="MEMORY.md soft token guardrail (default 1500)",
    )
    args = p.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    sanctum = sanctum_home() / "_bmad" / "memory" / SKILL_NAME
    if not sanctum.is_dir():
        print(json.dumps({"error": "no sanctum", "sanctum": str(sanctum)}))
        return 1

    today = date.today()
    report = {
        "sanctum": str(sanctum),
        "invoked_from": str(project_root),
        "born": (sanctum / BORN_MARKER).is_file(),
        "checked_on": today.isoformat(),
        "memory_md": measure_memory(sanctum, args.guardrail),
        "session_logs": stale_session_logs(sanctum, args.days, today),
        "index_drift": index_drift(sanctum),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
