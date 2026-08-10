# Personal Agent Template

A template for building a private AI agent with durable, file-backed memory across sessions. Plain
Markdown holds the memory. Tested scripts initialize, load, and audit it deterministically. You
define the identity.

The repository is named `personal-agent-template`. The skill and sanctum use `local-agent` as their
stable mechanical name. Everything the owner sees is customizable: name, icon, title, voice,
mission, context, capabilities, and specialist integrations.

## Why

- Memory is durable Markdown on your machine, inspectable, with optional Git history and rollback.
- The lifecycle is deterministic. Tested scripts scaffold, load, and audit the same state every time.
- Memory hygiene is enforced rather than hoped for. Waking prices its own context and tells the agent when to curate.
- Agent Builder gives it any name, voice, and mission while the mechanism underneath stays constant.
- New capabilities and specialist dispatch drop in without touching the tested core.

This repository ships the generic, tested parts: sanctum initialization and waking, curation guidance
and auditing, First Breath, Remember, Recall, templates, and tests. BMad Agent Builder creates the
owner-specific identity seed. First Breath personalizes the sanctum. The optional Module Builder
packages the finished skill for distribution.

## Quick Start

Requires Node.js 20.12+, Git, [`uv`](https://docs.astral.sh/uv/), and an AI tool supported by the
BMad installer (`npx bmad-method install --list-tools`).

1. Clone into the canonical home.

   ```bash
   git clone <template-repository-url> ~/local-agent
   cd ~/local-agent
   ```

2. Install BMad Builder, selecting **BMad Builder (BMB)**. Add specialist modules such as **Test
   Architect** if the owner uses them.

   ```bash
   npx bmad-method@latest install
   ```

3. Launch one of the installed AI tools from this folder, invoke `bmad-bmb-setup`, then
   `bmad-agent-builder`. Choose **Build Process (BP)** and point it at `skills/local-agent`. Tell it
   to preserve the existing scripts, tests, assets, and references. The
   [Full Guide](docs/full-guide.md) has a complete prompt for this step.

4. Install the generated skill. Run the installer again, choose **Modify Install**, keep your
   existing selections, and add `~/local-agent/skills` as a custom source. The installer discovers
   `local-agent` once Agent Builder has written its `SKILL.md`. See the
   [custom-module guide](https://docs.bmad-method.org/how-to/install-custom-modules/) for
   non-interactive alternatives.

5. Invoke the generated skill from `~/local-agent`. First activation routes to First Breath,
   scaffolds `_bmad/memory/local-agent/`, and personalizes the identity. Later activations load the
   saved sanctum.

## What Ships

```text
skills/local-agent/
├── assets/          BOND, CREED, INDEX, MEMORY, PERSONA templates
├── examples/        external-skill-dispatch.md
├── references/      capability-authoring, curation-pass, first-breath,
│                    memory-guidance, prompt-quality-canon, recall, remember
└── scripts/         _sanctum.py, curate.py, init-sanctum.py, wake.py, tests/
```

Deliberately omitted, because they are owner-specific or generated:

- `skills/local-agent/SKILL.md`, written by Agent Builder
- `skills/local-agent/customize.toml`, which memory agents disable by default
- `_bmad/` installer output and `_bmad/memory/local-agent/` runtime memory
- prior assistant exports and raw imports

The sanctum is the primary customization surface. Reach for `customize.toml` only when a requirement
genuinely cannot live there.

## How It Runs

Nothing runs in the background and nothing watches the filesystem. The generated `SKILL.md` is the
entry point, and it tells the AI tool when to invoke the scripts with `uv`. Until Agent Builder
writes that file, the scripts sit inert with nothing to call them.

```text
tool discovers SKILL.md (by frontmatter)
└── you invoke the skill  (e.g. /local-agent)
    └── SKILL.md "On Activation" → uv run scripts/wake.py {project-root}
        ├── FIRST_BREATH        → references/first-breath.md → init-sanctum.py
        ├── FIRST_BREATH_RESUME → references/first-breath.md → continue saved work
        └── WAKING              → identity loads
                                └── CURATION DUE? → references/curation-pass.md → curate.py
```

Four scripts run the mechanism, with no overlap between them:

| Script            | Role     | Runs               | Effect                |
| ----------------- | -------- | ------------------ | --------------------- |
| `init-sanctum.py` | Builder  | First Breath, once | Scaffolds the sanctum |
| `wake.py`         | Router   | Every activation   | Reads only            |
| `curate.py`       | Auditor  | During curation    | Reads only            |
| `_sanctum.py`     | Contract | Imported by both   | No side effects       |

**`init-sanctum.py`** creates the folder structure, copies templates with config values substituted,
and generates `CAPABILITIES.md` from capability frontmatter. It is idempotent and exits untouched if
a sanctum already exists. This is the only time a script writes the sanctum; everything after is the
agent editing its own memory.

**`wake.py`** picks one mode from filesystem state alone:

```text
Activation
└── wake.py (canonical home, read-only)
    ├── no scaffold         → FIRST_BREATH        → load references/first-breath.md
    ├── scaffold, no .born  → FIRST_BREATH_RESUME → resume the interrupted birth
    └── scaffold + .born    → WAKING              → print the identity files,
                                                    then CURATION DUE if a threshold trips
```

`.born` is the handshake between builder and router. `init-sanctum.py` lays down placeholders and
never writes it; the conversational First Breath writes it last, once the identity is real. A
scaffold without `.born` means a birth was interrupted, so waking resumes it instead of greeting over
placeholders.

**`curate.py`** reports the numbers the agent cannot eyeball: `MEMORY.md` tokens against its
guardrail, the per-file and total cost of the waking load, whether `INDEX.md` is still an index,
session logs past the retention threshold, and files that have drifted out of `INDEX.md`. It never
edits. The pruning judgment stays with the agent.

**`_sanctum.py`** holds sanctum location, identity load order, thresholds, and the aged-log rule, so
the router and the auditor can never disagree about whether the sanctum is healthy.

## Memory Upkeep

Waking prices its own context. When the sanctum crosses a threshold, `wake.py` prints a
`CURATION DUE` block and the agent works through `references/curation-pass.md` before the session
ends.

```text
===== CURATION DUE =====
- MEMORY.md is ~2,329 tokens against a 1,500 guardrail
- 2 session logs are past 14 days and await distilling
```

No cron, no launchd job, no hook, and nothing to reinstall on a second machine, so there is nothing
that can silently stop firing.

Two rules make the difference between lean memory and lost memory:

- **Compression is relocation.** Detail moves into the file that owns the topic, then the pointer
  gets shortened. Shrinking a file by dropping what it knows is amnesia with better numbers.
- **Price every file that loads on waking.** A guardrail on `MEMORY.md` alone does not stop growth,
  it relocates growth into whichever loaded file nothing measures.

Check it directly at any time, from the repository root:

```bash
uv run skills/local-agent/scripts/curate.py .          # exact numbers
uv run skills/local-agent/scripts/wake.py . | tail -8  # what the agent sees
```

## The Canonical Home

`wake.py` and `curate.py` resolve one fixed sanctum home and ignore the invocation directory, so an
unrelated project can never trigger a false First Breath or spawn a second identity. It defaults to
`~/local-agent`. Set `LOCAL_AGENT_HOME` when the repository lives elsewhere:

```bash
export LOCAL_AGENT_HOME=/absolute/path/to/local-agent
```

The `project-root` argument is situational context for the agent and never relocates the sanctum. It
matters mechanically in exactly one place: `init-sanctum.py` scaffolds under `<project-root>/_bmad`,
so First Breath must run with the canonical home as its project root.

## Tests

```bash
uv run --with pytest --with tiktoken python -m pytest skills/local-agent/scripts/tests -q
```

## Quick Answers

**Why local files instead of hosted assistant memory?** Plain Markdown gives inspectable state,
version history, portability, and direct control over retention. The same approved files serve any
tool that can load the skill.

**Does local storage mean the content stays on the device?** No. The AI tool and model provider
receive whatever context is read from these files. Review their data handling, follow your
organization's policy, and keep credentials out of the sanctum.

**Is it safe?** The design is transparent and auditable. It is not a security boundary. File
permissions, Git remotes, device security, provider terms, and your own review discipline determine
the real posture.

**What does it cost?** The template is plain files and open tooling. AI tools, model usage, private
Git hosting, and specialist modules carry their own costs. The design is reversible, since the
durable state is ordinary files.

**Why ship a template at all?** Lifecycle code should be deterministic. Recreating wake,
initialization, and curation scripts on every Agent Builder run revives bugs that were already
solved.

## Full Guide

The [Full Guide](docs/full-guide.md) covers prerequisites, repository creation, BMad Builder
installation, customization paths, specialist dispatch, Agent Builder, Module Builder packaging,
lifecycle tests, skill installation, First Breath, importing prior memory, end-to-end verification,
daily memory discipline, moving to another device, and safe BMad updates. It also carries the
[Definition of Done](docs/full-guide.md#definition-of-done) and
[official BMad references](docs/full-guide.md#official-references).
