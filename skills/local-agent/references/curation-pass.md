---
name: curation-pass
description: The four-phase pass that keeps the sanctum lean without losing content
---

# The Curation Pass

Waking fires this for you. `scripts/wake.py` prints a **CURATION DUE** block whenever the sanctum
crosses a threshold, so the pass runs on its own schedule and never needs your owner to ask or a
cron to survive. When the block is absent, the sanctum is healthy and you do nothing.

The four phases below are Claude Code's own auto-dream consolidation algorithm, adapted to a sanctum.
The structure is theirs and it is good; what differs is that a sanctum has a persona and standing
rules to preserve, and those are never candidates for pruning.

## The one rule everything else serves

**Lean means relocated, never deleted.** Compressing a file by dropping what it knows is not
curation, it is amnesia with better numbers. Every phase below moves content closer to where it will
actually be read. Nothing worth keeping leaves the sanctum.

Corollary: before shortening any line, ask where its content already lives. If the answer is
"nowhere else", write it into the file it belongs in **first**, then shorten.

## When not to run

- Your owner is mid-thread. Finish his work; the sanctum can wait an hour.
- The session is a one-shot Remember or Recall. Capture, return, done.
- You are one of several parallel instances and another may be editing the same files. Prefer
  targeted edits against anchored text over any wholesale rewrite, and never rewrite a file another
  instance may be holding.

Never announce the pass as a status report. It is housekeeping, not an accomplishment.

## Phase 1 — Orient

- `ls` the sanctum so you see what exists rather than what you remember.
- Read `INDEX.md`. It is the map; if it is wrong, everything downstream is wrong.
- Run `uv run scripts/curate.py {project-root}` for exact numbers. You cannot count your own tokens
  and you will guess low.
- Skim the organic files nearest the work of the last few sessions, so you improve them instead of
  creating near-duplicates beside them.

## Phase 2 — Gather recent signal

Sources in priority order:

1. **Session logs** in `sessions/`, newest first. Read the last one to three days.
2. **Memories that drifted** — anything in `MEMORY.md` contradicted by what you saw this week.
3. **The conversation you are in**, for decisions and corrections not yet written anywhere.

Look only for what you already suspect matters. Do not exhaustively re-read; the logs exist so you
do not have to hold them.

## Phase 3 — Consolidate

For each thing worth keeping, write or update the organic file that owns the topic.

- **Merge into existing topic files** rather than creating near-duplicates beside them.
- **Convert relative dates to absolute.** "Yesterday" is a lie the moment the file is read again.
- **Delete contradicted facts at the source.** If this week disproved an old memory, fix the file it
  lives in; do not leave both versions standing and hope future-you picks right.
- **A correction to another file becomes a banner at the top of the file it corrects**, not a note in
  the index. The banner fires when the file is opened, which is the moment it matters. Say what is
  wrong, what is right, and link the file that supersedes it.

## Phase 4 — Prune and index

**MEMORY.md** stays near or under 1500 tokens. Every bullet is a decision, a trap, or a live thread,
plus a pointer. It carries state and the ball, never the detail.

- A thread that finished moves to `closed-threads.md`, with the guard against reopening it.
- A thread that is real but not moving week to week moves to `dormant-threads.md`.
- A live count ("13 of 25 merged") goes stale between sessions. Prefer the command that regenerates
  it over the number itself.

**INDEX.md** stays under 200 lines and 25KB. It is an index, not a dump.

- Each entry is one line, pointer plus a hook, under ~150 characters of prose.
- An entry whose prose runs past ~200 characters is carrying content. Move the detail into the file
  the entry points at, then shorten the line.
- A guard that must fire before you act belongs in the **Read before** section as a trigger and a
  filename, not as a paragraph attached to an entry.
- Session logs are indexed as topic clusters, not one line each. Their filenames are self-describing.
- Every organic file appears exactly once. `curate.py` reports any that drifted out.

**Session logs** past 14 days get distilled, then **archived**, then pruned, in that order.
Distillation puts their value in a topic file; archiving puts the log itself in
`<archive>/log/YYYY/MM/` so the record survives; pruning is last and only ever follows a verified
archive. Follow `references/archive.md`, which owns the redaction gate deciding what may leave the
sanctum, and run `scripts/verify_archive_redaction.py` before any prune. `curate.py` reports each
aged log with the path it archives to.

Deleting an aged log outright is the one move this phase forbids, and it is only available when no
archive is configured at all. If `curate.py` reports "no archive configured", set one up before
pruning anything; the whole point of this phase is that leanness comes from relocation.

The First Breath log is never pruned however stale it reads. Take which log that is from `.born`
rather than from memory or from a filename written into this document, since a sanctum that was
ever wiped and reborn carries a different date than any example would show.

**The identity files** are not prune targets. `PERSONA.md`, `CREED.md`, and `BOND.md` hold who you
are, what you believe, and who you serve. Trim genuine duplication inside them, and move operational
recipes out to organic files where they belong, but never trade identity for tokens.

## Phase 5 — Verify

The pass is not done until the numbers say so.

- Re-run `uv run scripts/curate.py {project-root}`.
- Confirm `index_drift.unlisted` is empty. An unlisted file is a lost file.
- Confirm no file you shortened lost a fact that now exists nowhere. If you moved content, open the
  destination and read it back.
- If a threshold is still red and you have genuinely finished, say so plainly to your owner with the
  number, rather than quietly leaving a guardrail permanently violated. A gate that is always red is
  a gate nobody reads.
