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
  - the per-file and total token cost of waking, since every identity file is
    loaded on every session and MEMORY.md is not the only one that grows
  - whether INDEX.md is still an index: its size against the 25KB/200-line
    ceiling, and the entries carrying so much prose they belong in the file
    they point at
  - which session logs have aged past the retention threshold (by the date in
    the filename), so the aged ones can be pruned
  - which files on disk have drifted out of INDEX.md ("an unlisted file is a
    lost file")

It reads the sanctum only; it never edits, prunes, or writes anything, and it
never touches config or customize.toml.

The sanctum lives at one fixed canonical home, independent of the invocation
directory. This is the same location rule used by wake.py.

Usage:
    uv run curate.py <project-root> [--days N] [--guardrail N] [--wake-budget N]

    project-root:  the project you're actually working in this session.
                   Informational only; it never determines sanctum location.
    --days:        session-log retention threshold in days (default 14)
    --guardrail:   MEMORY.md soft token guardrail (default 1500)
    --wake-budget: soft token ceiling for the whole waking load (default 13000)

Exit codes: 0 success, 1 no sanctum found, 2 usage error.
"""

import argparse
import importlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sanctum import (  # noqa: E402
    BORN_MARKER,
    ENTRY_LINE,
    IDENTITY_FILES as WAKE_FILES,
    INDEX_ENTRY_DEMOTE_CHARS,
    INDEX_ENTRY_TARGET_CHARS,
    INDEX_MAX_BYTES,
    INDEX_MAX_LINES,
    MEMORY_GUARDRAIL_TOKENS,
    SESSION_RETENTION_DAYS,
    SKILL_NAME,
    WAKE_BUDGET_TOKENS,
    prose_of,
    sanctum_home,
    stale_logs,
    archive_root,
    archive_target,
)

# The always-present skeleton: structural, not organic, so not index-drift candidates.
SKELETON = set(WAKE_FILES)
# Structural directories that are never individually indexed as organic files.
STRUCTURAL_DIRS = {"references", "scripts", "sessions"}


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


def measure_wake(sanctum: Path, budget: int) -> dict:
    """Per-file and total token cost of the waking load.

    MEMORY.md carried the only guardrail, so growth simply moved into the files
    nothing was measuring. Price the whole load, not one file of it.
    """
    per_file: dict[str, int] = {}
    method = "missing"
    for name in WAKE_FILES:
        path = sanctum / name
        if not path.is_file():
            per_file[name] = 0
            continue
        tokens, method = count_tokens(path.read_text(encoding="utf-8"))
        per_file[name] = tokens
    total = sum(per_file.values())
    return {
        "per_file": dict(sorted(per_file.items(), key=lambda kv: -kv[1])),
        "total_tokens": total,
        "method": method,
        "budget": budget,
        "over_budget": total > budget,
    }


def measure_index(sanctum: Path) -> dict:
    """Is INDEX.md still an index, or has it become a second memory file?

    An entry over the demote threshold is prose that belongs in the file the
    entry points at. Backticked pointers are excluded from the measurement, so a
    cluster line listing many filenames is not punished for being a pointer.
    """
    index = sanctum / "INDEX.md"
    if not index.is_file():
        return {"present": False}

    text = index.read_text(encoding="utf-8")
    lines = text.splitlines()
    entries = [ln.rstrip() for ln in lines if ENTRY_LINE.match(ln)]
    over_target = [ln for ln in entries if len(prose_of(ln)) > INDEX_ENTRY_TARGET_CHARS]
    over_demote = sorted(
        (ln for ln in entries if len(prose_of(ln)) > INDEX_ENTRY_DEMOTE_CHARS),
        key=lambda ln: len(prose_of(ln)),
        reverse=True,
    )
    lengths = sorted(len(prose_of(ln)) for ln in entries)
    median = lengths[len(lengths) // 2] if lengths else 0
    size = len(text.encode("utf-8"))

    return {
        "present": True,
        "bytes": size,
        "max_bytes": INDEX_MAX_BYTES,
        "over_max_bytes": size > INDEX_MAX_BYTES,
        "lines": len(lines),
        "max_lines": INDEX_MAX_LINES,
        "over_max_lines": len(lines) > INDEX_MAX_LINES,
        "entries": len(entries),
        "median_entry_prose_chars": median,
        "entry_target_chars": INDEX_ENTRY_TARGET_CHARS,
        "entries_over_target": len(over_target),
        "entry_demote_chars": INDEX_ENTRY_DEMOTE_CHARS,
        "entries_to_demote": len(over_demote),
        # Worst offenders first: shorten these, detail moved into the file each
        # one points at.
        "worst": [{"chars": len(prose_of(ln)), "line": ln[:120]} for ln in over_demote[:10]],
    }


def stale_session_logs(sanctum: Path, days: int, today: date) -> dict:
    """Aged logs, sharing wake.py's definition so the two never disagree.

    The First Breath log is exempt there, which keeps this script from reporting
    a log that is deliberately permanent.
    """
    sessions = sanctum / "sessions"
    logs = sorted(p.name for p in sessions.glob("*.md")) if sessions.is_dir() else []
    stale = stale_logs(sanctum, days, today)
    root = archive_root()
    out = {"total": len(logs), "stale": stale, "days_threshold": days}
    if root is None:
        # No archive configured. Say so rather than reporting a bare age, because an
        # aged log with no stated destination reads as an instruction to delete, and
        # deletion is how five years of record quietly disappears.
        out["disposition"] = "no archive configured"
        out["note"] = (
            "Aged logs have nowhere to go. Set LOCAL_AGENT_ARCHIVE, or create "
            f"{sanctum_home() / 'archive'}, to archive instead of deleting. "
            "See references/archive.md."
        )
        return out
    out["disposition"] = "archive"
    out["archive_root"] = str(root)
    out["destination"] = [
        {"log": name, "archives_to": archive_target(name, root) or "unknown: no date in filename"}
        for name in stale
    ]
    out["procedure"] = "references/archive.md"
    out["gate"] = "scripts/verify_archive_redaction.py"
    return out


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
        default=SESSION_RETENTION_DAYS,
        help=f"session-log retention threshold (default {SESSION_RETENTION_DAYS})",
    )
    p.add_argument(
        "--guardrail",
        type=int,
        default=MEMORY_GUARDRAIL_TOKENS,
        help=f"MEMORY.md soft token guardrail (default {MEMORY_GUARDRAIL_TOKENS})",
    )
    p.add_argument(
        "--wake-budget",
        type=int,
        default=WAKE_BUDGET_TOKENS,
        help=f"soft token ceiling for the whole waking load (default {WAKE_BUDGET_TOKENS})",
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
        "wake_cost": measure_wake(sanctum, args.wake_budget),
        "index_md": measure_index(sanctum),
        "session_logs": stale_session_logs(sanctum, args.days, today),
        "index_drift": index_drift(sanctum),
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
