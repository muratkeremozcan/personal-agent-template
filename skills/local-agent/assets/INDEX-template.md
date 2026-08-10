# Index

Pointers only. One line per file, under ~150 characters. Detail lives in the file it points at,
never here. Two rules keep it that way:

- A guard that must fire before I act lives in **Read before**, as a trigger and a filename.
- A correction to another file lives as a **banner at the top of the file it corrects**, not here.

If a line here starts carrying content, the content belongs in the file. This index stays under
200 lines and 25KB; `uv run scripts/curate.py {project-root}` reports every entry that has drifted.

## Standard Files

- `PERSONA.md`: who I am (name, vibe, style, evolution log)
- `CREED.md`: what I believe (values, philosophy, boundaries, dominion)
- `BOND.md`: who I serve (my owner: role, work, and life context)
- `MEMORY.md`: what I know (curated long-term knowledge)
- `CAPABILITIES.md`: what I can do (built-in + learned abilities + tools)
- `sessions/`: raw notes by date, curated into MEMORY.md and pruned after 14 days

## Read before

Load the named file before acting on the trigger. These are the traps that cost me something once.
Keep each line to a trigger and a filename; the reasoning belongs in the file.

_Empty until the first trap earns a line._

## Knowledge

- `knowledge/`: detailed approved material that should not load on every waking.
  Register every file below under My Files.

## My Files

_This section grows as I create organic files. Update it when adding new files._

## Session Logs

_Once these outgrow a handful, index them as topic clusters rather than one line each; the
filenames are self-describing, so a cluster line plus a grep finds any of them._
