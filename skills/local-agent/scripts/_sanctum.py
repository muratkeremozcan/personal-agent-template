#!/usr/bin/env python3
"""Shared sanctum constants and helpers.

wake.py and curate.py both need to know where the sanctum lives, what the
waking load costs, and when curation is due. Holding those numbers in one place
is what keeps the two from drifting into disagreeing about whether the sanctum
is healthy.

The failure this exists to prevent is specific and observed: MEMORY.md carried
the only token guardrail, so when curation pressure arrived the content simply
moved into INDEX.md, which nothing measured. INDEX.md reached three times
MEMORY.md's size and the waking load doubled while every reported number stayed
green. Measure the whole load, not one file of it.

Stdlib only, deliberately: wake.py runs on every activation and must stay fast
and dependency-free. curate.py adds tiktoken for exact counts on top of this.
"""

import os
import re
from datetime import date
from pathlib import Path

SKILL_NAME = "local-agent"
BORN_MARKER = ".born"

# Load order of the "become yourself" set — everything paid for on every session.
IDENTITY_FILES = [
    "INDEX.md",
    "PERSONA.md",
    "CREED.md",
    "BOND.md",
    "MEMORY.md",
    "CAPABILITIES.md",
]

# --- Thresholds -------------------------------------------------------------
# INDEX.md limits are Claude Code's own, lifted from the auto-dream
# consolidation prompt in the 2.1.226 binary (Phase 4 — "Prune and index"):
# an index stays under 200 lines and ~25KB, each entry is one line under ~150
# characters, and an entry over ~200 characters "is carrying content that
# belongs in the topic file".
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024
INDEX_ENTRY_TARGET_CHARS = 150
INDEX_ENTRY_DEMOTE_CHARS = 200

# Deliberately tight for a new agent, which has few live threads and should learn
# the habit early.
#
# Raising either number is a last resort with a recorded reason. Run the curation
# pass first. If MEMORY.md is still over and every remaining line is load-bearing
# (a decision, a trap, or a live thread with a ball), raise it and write down here
# what the extra tokens buy. A guardrail that moves whenever it goes red measures
# nothing, so the reason is the point, not the number.
MEMORY_GUARDRAIL_TOKENS = 1500
WAKE_BUDGET_TOKENS = 13000
SESSION_RETENTION_DAYS = 14

# wake.py has no tiktoken, so it estimates. ~4 bytes per token holds well for
# English markdown; curate.py remains the exact source of truth.
BYTES_PER_TOKEN = 4

ENTRY_LINE = re.compile(r"^\s*[-*]\s+\S")
BACKTICKED = re.compile(r"`[^`]*`")
DATE_IN_NAME = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def sanctum_home() -> Path:
    """Canonical sanctum home: $LOCAL_AGENT_HOME if set, else ~/local-agent.

    Independent of invocation cwd/project-root on purpose — one sanctum, not one
    per repo. A per-repo sanctum would false-start First Breath any time the
    agent is invoked from another repo, which happened once.
    """
    override = os.environ.get("LOCAL_AGENT_HOME")
    home = Path(override) if override else Path.home() / "local-agent"
    return home.expanduser().resolve()


def sanctum_path() -> Path:
    return sanctum_home() / "_bmad" / "memory" / SKILL_NAME


def archive_root() -> Path | None:
    """The cold archive, if the owner has set one up.

    The sanctum is bounded on purpose: it loads on every waking, so every token in
    it costs context that the actual conversation could have used. That bound is
    what forces curation, and curation is what has historically destroyed history,
    because an aged session log had nowhere to go except deletion.

    An archive is any directory of markdown outside the sanctum with no token
    budget and no retention limit. It is optional. When it is absent the agent
    behaves exactly as before, and `curate.py` says plainly that aged logs have no
    destination, so the choice to delete stays deliberate rather than implied.

    $LOCAL_AGENT_ARCHIVE overrides. The default sits beside the sanctum rather
    than inside it, because anything inside would be loaded, counted and curated
    like identity, which is the opposite of what an archive is for.
    """
    override = os.environ.get("LOCAL_AGENT_ARCHIVE")
    root = Path(override) if override else sanctum_home() / "archive"
    root = root.expanduser().resolve()
    return root if root.is_dir() else None


def archive_target(filename: str, root: Path) -> str | None:
    """Where an aged log lands in the archive, from the date in its filename.

    Uses the same date-in-filename rule `stale_logs` uses to decide a log is aged,
    so the aged list and the archive location can never disagree about which month
    a log belongs to. A filename carrying no date has no derivable target; that is
    reported rather than guessed at.
    """
    m = DATE_IN_NAME.search(filename)
    if not m:
        return None
    return str(root / "log" / m.group(1) / m.group(2) / filename)


def prose_of(entry: str) -> str:
    """An index entry minus its backticked pointers.

    A cluster line that is mostly filenames is still a pointer however long it
    runs; what makes an entry too long is prose, which is content that belongs
    in the file the entry points at. Measuring the whole line would flag the
    cheap lines forever, and a guardrail that is always red gets ignored.
    """
    return BACKTICKED.sub("", entry).strip()


def birth_date(sanctum: Path) -> date | None:
    """The date First Breath completed, from the `.born` marker it wrote."""
    marker = sanctum / BORN_MARKER
    if not marker.is_file():
        return None
    m = DATE_IN_NAME.search(marker.read_text(encoding="utf-8").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def stale_logs(sanctum: Path, days: int, today: date) -> list[str]:
    """Session logs whose filename date is older than the retention threshold.

    The First Breath log is exempt however old it gets: its facts are distilled
    elsewhere, and the record of being born is continuity rather than a fact.
    It is identified by the date in the `.born` marker, so no filename is
    hardcoded and the rule holds for any agent built from this template.
    """
    sessions = sanctum / "sessions"
    if not sessions.is_dir():
        return []
    born = birth_date(sanctum)
    out = []
    for path in sorted(sessions.glob("*.md")):
        m = DATE_IN_NAME.search(path.name)
        if born and m and m.group(0) == born.isoformat():
            continue
        if not m:
            continue
        try:
            log_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if (today - log_date).days > days:
            out.append(path.name)
    return out


def health(sanctum: Path, today: date | None = None) -> dict:
    """Cheap, stdlib-only read of whether curation is due.

    Byte-based so wake.py can call it on every activation without paying for
    tiktoken. Returns the reasons curation is due; an empty `reasons` means the
    sanctum is healthy and waking says nothing.
    """
    today = today or date.today()

    per_file = {}
    for name in IDENTITY_FILES:
        path = sanctum / name
        per_file[name] = path.stat().st_size if path.is_file() else 0
    total_bytes = sum(per_file.values())
    total_tokens = total_bytes // BYTES_PER_TOKEN

    index = sanctum / "INDEX.md"
    index_bytes = per_file.get("INDEX.md", 0)
    index_lines = 0
    fat_entries = 0
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        index_lines = len(text.splitlines())
        fat_entries = sum(
            1
            for ln in text.splitlines()
            if ENTRY_LINE.match(ln) and len(prose_of(ln)) > INDEX_ENTRY_DEMOTE_CHARS
        )

    memory_tokens = per_file.get("MEMORY.md", 0) // BYTES_PER_TOKEN
    aged = stale_logs(sanctum, SESSION_RETENTION_DAYS, today)

    reasons = []
    if total_tokens > WAKE_BUDGET_TOKENS:
        reasons.append(
            f"waking costs ~{total_tokens:,} tokens against a {WAKE_BUDGET_TOKENS:,} budget"
        )
    if memory_tokens > MEMORY_GUARDRAIL_TOKENS:
        reasons.append(
            f"MEMORY.md is ~{memory_tokens:,} tokens against a {MEMORY_GUARDRAIL_TOKENS:,} guardrail"
        )
    if index_bytes > INDEX_MAX_BYTES:
        reasons.append(
            f"INDEX.md is {index_bytes // 1024}KB against a {INDEX_MAX_BYTES // 1024}KB ceiling"
        )
    if index_lines > INDEX_MAX_LINES:
        reasons.append(f"INDEX.md is {index_lines} lines against a {INDEX_MAX_LINES} ceiling")
    if fat_entries:
        reasons.append(
            f"{fat_entries} INDEX.md entries carry prose over {INDEX_ENTRY_DEMOTE_CHARS} chars "
            "and belong in the file they point at"
        )
    if aged:
        reasons.append(
            f"{len(aged)} session logs are past {SESSION_RETENTION_DAYS} days and await distilling"
        )

    return {
        "estimated_wake_tokens": total_tokens,
        "estimated_memory_tokens": memory_tokens,
        "index_bytes": index_bytes,
        "index_lines": index_lines,
        "fat_index_entries": fat_entries,
        "aged_logs": aged,
        "reasons": reasons,
        "curation_due": bool(reasons),
    }
