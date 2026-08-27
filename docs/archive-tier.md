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

`curate.py` picks it up automatically. With no archive configured it says so explicitly
rather than reporting a bare age, because an aged log with no stated destination reads as an
instruction to delete.

```json
"session_logs": {
  "stale": ["2026-05-04-topic.md"],
  "disposition": "archive",
  "destination": [
    {"log": "2026-05-04-topic.md", "archives_to": "~/local-agent/archive/log/2026/05/2026-05-04-topic.md"}
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

## Obsidian, if you want the graph

Nothing above needs Obsidian. An archive is markdown in folders and any editor reads it.

What Obsidian adds is that `[[wikilinks]]` in the archived frontmatter become a navigable
graph. Archive a year of session logs with `people:`, `themes:` and `repos:` lists, and
clusters appear on their own: the people who recur together, the themes that bridge two jobs,
the repository that every incident traces back to. That is a tool for the owner rather than
for the agent, which reads the files directly either way.

Two properties make it work well as an archive:

- **Plain files, no lock-in.** No database and no proprietary format. Everything the graph
  knows is derived from text on disk, so the agent needs no integration to read or write it.
- **Unresolved links are free nodes.** A link to `[[TICKET-123]]` is a graph node whether or
  not a file by that name exists. Hundreds of tickets and repositories reach the graph at no
  cost in files, and writing a stub for each that said "mentioned once" would be worse in
  every way.

To point the agent at a vault, set `LOCAL_AGENT_ARCHIVE` to it and the archive lands in
`<vault>/log/YYYY/MM/`.

An MCP server is available (the Local REST API plugin serves one) and is worth having for one
specific reason: it exposes Obsidian's own resolved link index, so link-integrity checks stop
depending on a regex approximation of Obsidian's parser. It is not required, it needs the app
running, and it adds nothing to what the agent can remember.
