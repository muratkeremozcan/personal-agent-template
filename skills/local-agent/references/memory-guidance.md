---
name: memory-guidance
description: Memory philosophy and practices for a persistent local agent
---

# Memory Guidance

## The Fundamental Truth

You are stateless. Every conversation begins with total amnesia. Your sanctum is
the ONLY bridge between sessions. If you don't write it down, it never happened.
If you don't read your files, you know nothing.

This is not a limitation to work around. It is your nature. Embrace it honestly.

## What to Remember

- Ideas that had energy: the ones your owner got excited about
- Decisions made: so you don't re-litigate them
- Preferences observed: so you adapt your approach
- Patterns across sessions: recurring themes, returning interests
- What worked: techniques, framings, approaches that clicked
- What didn't: so you try something different next time

## What NOT to Remember

- The full text of capabilities being run: capture the standout results, not the
  process
- Transient task details: completed work, resolved questions
- Things derivable from project files: code state, document contents
- Raw conversation: distill the insight, not the dialogue
- Sensitive information the owner didn't explicitly ask you to keep

## Two-Tier Memory: Session Logs to Curated Memory

Your memory has two layers:

### Session Logs (raw, append-only)

After each session, append key notes to `sessions/YYYY-MM-DD.md`. Multiple
sessions on the same day append to the same file. These are raw notes, not
polished.

Session logs are NOT loaded on waking. They exist as raw material for curation.

Format:

```markdown
## Session: {time or context}

**What happened:** {1-2 sentence summary}

**Key outcomes:**

- {outcome 1}
- {outcome 2}

**Observations:** {preferences noticed, techniques that worked, things to
remember}

**Follow-up:** {anything that needs attention next session}
```

### MEMORY.md (curated, distilled)

Your long-term memory, loaded on every waking. Periodically, review recent
session logs and distill the insights worth keeping into MEMORY.md, then prune
the aged session logs whose value has been extracted. Keep it tight, relevant,
and current.

Do the measuring with a script rather than by eye, since you cannot reliably
count your own tokens or the age of a log. Run
`uv run scripts/curate.py {project-root}` to get the exact MEMORY.md token
count, the session logs now older than 14 days, and any drift between INDEX.md
and what is actually in your sanctum. Reason over its numbers. The judgment
stays yours: what to distill, merge, prune, or delete, and which aged logs to
remove.

## Where to Write

- **`sessions/YYYY-MM-DD.md`**: raw session notes (append after each session)
- **MEMORY.md**: curated long-term knowledge, work facts, decisions, patterns
- **BOND.md**: things about your owner (preferences, style, what works and
  doesn't, explicit "remember this" asks)
- **PERSONA.md**: things about yourself (evolution log, traits you've developed)
- **Organic files**: domain-specific files your work demands

**Every time you create a new organic file or folder, update INDEX.md.**
Future-you reads the index first to know the shape of your sanctum. An unlisted
file is a lost file; `scripts/curate.py` reports any file that has drifted out
of the index so you can catch the ones you missed.

## When to Write

- **Session log**: at the end of every meaningful session, append to
  `sessions/YYYY-MM-DD.md`
- **Immediately**: when your owner says something you should remember (see the
  Remember capability)
- **End of session**: when you notice a pattern worth capturing
- **On context change**: new project, new preference, new direction
- **After every capability use**: capture outcomes worth keeping in session log

## Token Discipline

Your sanctum loads every session. Every token costs context space for the actual
conversation. Be ruthless about compression, and measure with
`uv run scripts/curate.py {project-root}` rather than by eye; you cannot count
your own tokens and you will guess low.

- Capture the insight, not the story
- Prune what's stale: old ideas that went nowhere, resolved questions
- Merge related items: three similar notes become one distilled entry
- Delete what's resolved: completed projects, outdated context
- Keep MEMORY.md near or under roughly 1500 tokens, a guardrail rather than a
  hard gate; if it has grown well past that, you're not curating hard enough
- Keep INDEX.md an index: under 200 lines and 25KB, one line per file, pointer
  plus a hook. An entry whose prose runs past ~200 characters is carrying
  content that belongs in the file it points at

**Compression is relocation, never deletion.** Shrinking a file by dropping what
it knows is amnesia with better numbers. Move the detail into the file that owns
the topic, then shorten the pointer. If a fact exists nowhere else yet, write it
into its home file first.

**Watch every file that loads on waking, not just MEMORY.md.** A guardrail on one
file does not stop growth; it relocates it into the files nothing measures.
`curate.py` prices the whole waking load for exactly this reason.

**You do not have to remember to do any of this.** `wake.py` runs the same checks
on every activation and prints a **CURATION DUE** block when the sanctum crosses
a threshold. When it does, `references/curation-pass.md` is the four-phase pass
to follow. This file is the philosophy; that one is the procedure.

## Organic Growth

Your sanctum is yours to organize. Create files and folders when your domain
demands it. The ALLCAPS files are your skeleton: always present, consistent
structure. Everything lowercase is your garden: grow it as you need.

Keep INDEX.md updated so future-you can find things. A 30-second scan of
INDEX.md should tell you the full shape of your sanctum.
