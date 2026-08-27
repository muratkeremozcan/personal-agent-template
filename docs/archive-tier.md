# The archive tier

Optional. The agent works without it and always has. This document explains what it buys,
what it costs, and how to set one up with or without Obsidian.

## The problem it solves

`MEMORY.md` loads on every waking, so every token in it costs context the actual
conversation could have used. That bound is deliberate and it is what forces curation.

The side effect is that curation had nowhere to put things. `curate.py` reported session
logs older than fourteen days, `curation-pass.md` said they get "distilled and then
removed", and removal meant the log was gone. The distillation kept the insight; the record
of what actually happened, in the owner's own words, was destroyed on a two-week timer.

An archive is any directory of markdown outside the sanctum. It has no token budget because
it is never loaded on waking, and no retention limit because nothing pays for its size. It
is the tier the memory system was missing:

| tier | loaded on waking | bounded | holds |
|---|---|---|---|
| `MEMORY.md` | yes | ~2500 tokens | what would make the agent wrong without it |
| session logs | no | 14 days | raw notes awaiting distillation |
| **archive** | **no** | **unbounded** | **every log, permanently** |

## Setting one up

Create a directory and point at it:

```bash
mkdir -p ~/local-agent/archive
# or anywhere else:
export LOCAL_AGENT_ARCHIVE=~/notes/agent-archive
```

`curate.py` picks it up automatically and prints resolved absolute paths, not `~` forms. With no archive configured it says so explicitly
rather than reporting a bare age, because an aged log with no stated destination reads as an
instruction to delete.

```json
"session_logs": {
  "stale": ["2026-05-04-topic.md"],
  "disposition": "archive",
  "destination": [
    {"log": "2026-05-04-topic.md", "archives_to": "/Users/you/local-agent/archive/log/2026/05/2026-05-04-topic.md"}
  ],
  "procedure": "references/archive.md",
  "gate": "scripts/verify_archive_redaction.py"
}
```

Then read `references/archive.md` once, in full, before the first archive. It owns the
redaction gate.

## The redaction gate is not optional

The archive is usually git-backed and may be replicated by a sync service. Anything that
passes the gate has left the machine and cannot be recalled.

Sanctum session logs contain things the owner said in confidence: what a colleague told them
about another colleague, a decision not yet announced, a number under embargo. The gate is
what stands between that material and a durable copy. `references/archive.md` specifies how
it decides, and `scripts/verify_archive_redaction.py` makes the outcome checkable rather than
asserted: it re-reads both files off disk and fails on a surviving sentence, on a seven-word
run that survived paraphrase, on a filename sharing distinctive tokens with the withheld
text, and on a missing withheld notice. It exits 2 when it cannot run at all, which blocks
the prune exactly as a failure does.

Three rules that are easy to get wrong and were, in a real deployment:

- **Withheld material must go somewhere.** Withhold a block from the archive, then prune the
  source log, and it exists nowhere. It goes to `sessions/redacted/`, which `stale_logs`
  never sees because its glob is non-recursive.
- **Frontmatter is computed from the redacted text, never the source.** A person named only
  inside a withheld block would otherwise appear in `people:` and put the subject of the
  redaction onto the graph, in the copy that leaves the machine.
- **The filename is taint too.** The slug, the `source` value and the withheld notice are
  three verbatim copies of the sanctum filename. A log named for its subject leaks it three
  times over while the body shows nothing.

## Entity notes: the part that changes what the agent can recall

This is the highest-value half of the archive tier and it is easy to mistake for decoration.

Archived logs carry `people:`, `themes:` and `repos:` frontmatter. Generate one small note per
entity, whose body is the list of every log referencing it, and each of those notes becomes a
**precomputed retrieval index**. It is worth being concrete about the difference. In one real
deployment, answering "when did contract testing happen, and where" cost:

| | files opened | tokens read | reliability |
|---|---|---|---|
| grep the archive | 22 | ~64,000 | a regex the agent invented; a miss is silent |
| open `theme/contract-testing.md` | 1 | ~227 | complete by construction |

**281 times less context, and correct rather than hopeful.** The index answered with a
five-year arc, December 2021 through August 2026, because it was built when the notes were
written rather than guessed at read time.

That is the argument for entity notes. An agent's recall is bounded by what it can afford to
read, so an index that collapses a full-text sweep into one file is not a convenience; it
decides whether a question about five years of history is answerable at all inside one
context window.

The indexes also compound. Every archived log adds itself to the entities it mentions, so the
answer to "what is the history with this person, this repository, this theme" gets better on
its own with no maintenance.

## Obsidian, if you want it

Nothing above needs Obsidian. An archive is markdown in folders, the entity notes are markdown
too, and any editor reads all of it. What Obsidian adds is threefold, in descending order of
value to the agent:

- **Backlinks maintained for free.** Obsidian resolves `[[wikilinks]]` continuously, so the
  entity indexes stay correct as an editor moves and renames things. Hand-maintained indexes
  rot; these do not.
- **Unresolved links are free nodes.** A link to `[[TICKET-123]]` resolves as a graph node
  whether or not a file by that name exists. Hundreds of tickets and repositories become
  navigable at no cost in files, and writing a stub for each saying "mentioned once" would be
  worse in every way.
- **The graph view.** Clusters appear on their own: people who recur together, themes that
  bridge two jobs, the repository every incident traces back to. This one is genuinely for the
  owner, and it is the part most likely to be mistaken for the whole feature.

Plain files and no lock-in throughout. There is no database and no proprietary format;
everything the graph knows is derived from text on disk, so the agent needs no integration to
read or write any of it.

To point the agent at a vault, set `LOCAL_AGENT_ARCHIVE` to it and the archive lands in
`<vault>/log/YYYY/MM/`.

An MCP server is available, since the Local REST API plugin serves one. It is worth having for
a narrow reason: it exposes Obsidian's own resolved link index, so link-integrity checks stop
depending on a regex approximation of Obsidian's parser, which is a real source of wrong
answers. It requires the app to be running, and it is the entity notes rather than the MCP that
change what the agent can recall.
