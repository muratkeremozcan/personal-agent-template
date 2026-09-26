# Personal Agent Template

Own your agent's memory.

This template builds an AI agent with a consistent identity and persistent
memory stored in files you control: plain Markdown in a Git repository you can
read, correct, diff, and roll back. Tested Python scripts create that memory,
reload it at the start of every session, and tell the agent when it needs
curating.

It is a foundation for developers, built on
[BMad Builder](https://bmad-builder-docs.bmad-method.org/). You decide the
agent's name, voice, mission, and capabilities; the template supplies the memory
machinery underneath.

## What It Looks Like

Four separate sessions with one agent built from this template, each started
fresh with no shared context. The replies are real and trimmed only for length.

**Session 1**

> **You:** Remember: my template is released under the Apache-2.0 license.
>
> **Agent:** I've saved that the template is released under Apache-2.0. One
> conflict: your local checkout has an untracked `LICENSE` file with MIT text.
> It needs the Apache-2.0 text before you commit it.

**Session 2**

> **You:** Which license is it released under, and which file holds that?
>
> **Agent:** Apache-2.0. You told me that on 2026-09-26, and it's stored in
> `MEMORY.md`. The conflict is still there: `LICENSE` currently contains MIT text.

**Session 3**

> **You:** Correction: it's the MIT license. Replace the old value.
>
> **Agent:** I've changed the stored license to MIT and removed the old
> Apache-2.0 fact. The `LICENSE` file already contains the MIT text, so the repo
> needs no change.

**Session 4**

> **You:** Which license is it released under, and which file holds that?
>
> **Agent:** The license is MIT. The fact lives in `MEMORY.md`, lines 102 to 104.

The remember and the correction each change a file you can inspect with `git diff`.

Run the same check on your own agent with
[Verify Memory Across Sessions](#verify-memory-across-sessions).

## Why Own the Memory

- Every fact the agent holds is a line of Markdown you can read.
- A wrong memory is a one-line fix, and Git shows when it changed and why.
- The memory moves with you, to another machine or another AI tool that runs
  skills.
- It stays small on purpose. `MEMORY.md` has a token guardrail, the agent curates
  itself when a threshold trips, and aged session logs can move to an archive
  that never loads into context.
- The identity survives restarts. Each session reloads the same persona, owner
  context, and memory, so the agent picks up the thread where it stopped.

Two honest limits:

- Whatever memory loads into a session goes to your model provider like any
  other prompt. Keep credentials and secrets out of the agent's files.
- Setup is developer-oriented: Git, `uv`, Node.js, and the BMad installer.

## About This Repository

This public template contains only generic machinery. It must never contain an
owner's identity or memory. Create a separate private repository before BMad
Agent Builder personalizes it and writes `skills/local-agent/SKILL.md`.

The resulting agent can have any display name. `local-agent` remains the stable
internal name used by the scripts and memory path.

## What You Get

- Markdown templates seed the identity, owner context, memory, and index.
- `init-sanctum.py` creates the memory store during First Breath.
- `wake.py` loads the same identity and memory on every activation.
- `curate.py` reports memory size, stale logs, and index drift.
- The tests cover initialization, waking, curation, and lifecycle behavior.

Nothing runs in the background. The generated skill invokes these scripts when
you activate the agent.

## Before You Start

Install:

- Node.js 20.12 or newer
- Git
- [`uv`](https://docs.astral.sh/uv/)
- An AI coding tool supported by BMad

Check your setup:

```bash
node --version
git --version
uv --version
npx bmad-method install --list-tools
```

Keep this template repository generic. First Breath and later sessions write
owner-specific identity and memory into the derived repository, which should
use a private remote or remain local. The AI tool and model provider will
receive memory loaded into context, so keep credentials and secrets out of the
agent's files.

## Setup

### 1. Create the Agent Repository

Create a new private repository from this template, then clone the private copy
to the default memory location. Replace `<private-repository-url>` with the URL
of that new repository.

```bash
git clone <private-repository-url> ~/local-agent
cd ~/local-agent
```

If your Git host cannot copy a template, clone this repository and immediately
replace its remote before running BMad or First Breath:

```bash
git clone <template-repository-url> ~/local-agent
cd ~/local-agent
git remote set-url origin <private-repository-url>
git push -u origin main
```

If you use another location, set `LOCAL_AGENT_HOME` to its absolute path before
running the agent:

```bash
export LOCAL_AGENT_HOME=/absolute/path/to/local-agent
```

### 2. Install BMad Builder

Run the installer from the repository root:

```bash
npx bmad-method install
```

In the installer:

1. Select **BMad Builder (BMB)**.
2. Select the AI tool you use.
3. Keep the installation scoped to this repository.

Then open the selected AI tool in this folder and invoke `bmad-bmb-setup` once.

### 3. Personalize the Agent

Invoke `bmad-agent-builder` and choose **Build Process (BP)** in guided mode.
Point it to the absolute path of `skills/local-agent`.

Tell Agent Builder to preserve the existing scripts, tests, assets, and
references. It should create the owner-specific identity layer, including
`skills/local-agent/SKILL.md`. Use the complete
[Agent Builder prompt](docs/full-guide.md#6-customize-with-agent-builder) when
you want a reliable copy-and-paste starting point.

Before continuing, confirm that this file now exists:

```text
skills/local-agent/SKILL.md
```

### 4. Run the Lifecycle Tests

```bash
uv run --with pytest --with tiktoken \
  python -m pytest skills/local-agent/scripts/tests -q
```

### 5. Install the Generated Skill

Run the BMad installer again:

```bash
npx bmad-method install
```

Choose the full modification flow, preserve your existing selections, and add
`~/local-agent/skills` as a custom local source. The installer discovers
`local-agent` from the `SKILL.md` created in step 3. The official
[custom source guide](https://docs.bmad-method.org/how-to/install-custom-modules/)
also covers command-line installation and other directory layouts.

To skip the installer, link the checkout straight into every AI tool's
user-level skill folder:

```bash
uv run skills/local-agent/scripts/install_global.py
```

It only creates relative symlinks and refuses to replace a real directory, so an
installer-managed copy is never overwritten.

Restart or reload your AI tool if the skill does not appear immediately.

### 6. Complete First Breath

Invoke `local-agent` from `~/local-agent` using your AI tool's normal skill
syntax. On its first activation, the agent will:

1. Create `_bmad/memory/local-agent/`.
2. Ask about its identity, mission, working style, and memory boundaries.
3. Write the resulting Markdown files.
4. Create `.born` last to mark First Breath complete.

If the session stops early, invoke the agent again. It will resume First Breath
from the saved state.

Review the new files before committing them:

```bash
git status
git diff
```

## Verify Memory Across Sessions

Use separate sessions for this check:

1. Ask the agent to remember a harmless preference.
2. Start a new session and ask it to recall that preference and name the source
   file.
3. Correct the preference.
4. Start another session and confirm that the current value replaced the old
   one.

This tests the user-facing flow that matters most: remember, reload, recall, and
correct.

## Where the Data Lives

The runtime memory store is:

```text
~/local-agent/_bmad/memory/local-agent/
├── INDEX.md
├── PERSONA.md
├── CREED.md
├── BOND.md
├── MEMORY.md
├── CAPABILITIES.md
├── capabilities/
├── knowledge/
├── references/
├── scripts/
└── sessions/
```

The repository intentionally tracks this directory so Git can provide history
and rollback. The optional `knowledge/private/` directory is ignored as a
device-local convention. File permissions, remote access, device security, and
provider policies still determine the real privacy boundary.

`wake.py` and `curate.py` always use the canonical home, regardless of the
project where the agent was invoked. The default is `~/local-agent`;
`LOCAL_AGENT_HOME` overrides it. This prevents a second identity from being
created when you invoke the agent from another project.

### The archive tier, optional

`MEMORY.md` loads on every waking, so it is deliberately bounded, and that bound
is what forces curation. Without somewhere to put them, curation deletes aged
session logs after fourteen days, and the record of what happened goes with them.

An archive is any directory of markdown outside the sanctum. It is never loaded
on waking, so it costs no context and has no size limit.

```bash
mkdir -p ~/local-agent/archive        # or: export LOCAL_AGENT_ARCHIVE=~/notes/archive
```

`curate.py` then reports each aged log with the path it archives to.

The template ships the archive tier itself: the destination, the procedure in
`references/archive.md`, and the redaction gate on what may leave the sanctum.
`scripts/verify_archive_redaction.py` checks that gate mechanically before any
source log is pruned. Entity notes (one note per person, theme, or repository,
linked from the archived logs) are a pattern the tier enables; what counts as an
entity is deployment-specific, so the template leaves the generator to you.
[`docs/archive-tier.md`](docs/archive-tier.md) explains the pattern, measures it
in one deployment, and covers what an Obsidian vault adds.

## Lifecycle

- No memory store: `FIRST_BREATH` starts initialization.
- Memory store without `.born`: `FIRST_BREATH_RESUME` continues initialization.
- Memory store with `.born`: `WAKING` loads the identity and memory files.

Every wake also checks lightweight memory guardrails. When curation is due, the
generated skill loads the curation instructions and runs the exact audit before
making model-led edits.

Run the read-only diagnostics yourself at any time:

```bash
uv run skills/local-agent/scripts/wake.py .
uv run skills/local-agent/scripts/curate.py .
```

## Repository Layout

```text
skills/local-agent/
├── assets/       Seed templates for the runtime memory store
├── examples/     Optional specialist dispatch example
├── references/   First Breath, memory, curation, and capability guidance
└── scripts/      Lifecycle scripts and tests
docs/
├── archive-tier.md
└── full-guide.md
ruff.toml         Pinned lint rules, run in CI
LICENSE           MIT
```

The template deliberately omits generated or owner-specific content:

- `skills/local-agent/SKILL.md`, created by Agent Builder
- `_bmad/`, created by the BMad installer and First Breath
- Raw assistant exports and imported memory
- Optional Module Builder packaging

Never commit those files back to this public template repository.

## Advanced Use

Read the [Full Guide](docs/full-guide.md) for existing-assistant migration,
specialist dispatch, optional Module Builder packaging, detailed end-to-end
checks, memory maintenance, device moves, safe BMad updates, and the complete
definition of done.

Current BMad behavior is documented in the official
[installation guide](https://docs.bmad-method.org/how-to/install-bmad/) and
[Builder command reference](https://bmad-builder-docs.bmad-method.org/reference/builder-commands/).

## License

[MIT](LICENSE)
