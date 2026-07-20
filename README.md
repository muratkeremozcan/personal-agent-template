# Personal Agent Template

A template for building a private AI agent with durable, file-backed memory
across sessions. Plain Markdown holds the memory. Tested scripts initialize,
load, and audit it deterministically. You define the agent's identity and can
expand its skills over time.

## What Makes It Different

- **Local, inspectable memory:** durable Markdown on your machine, with optional
  Git history and rollback.
- **Deterministic lifecycle:** tested scripts scaffold, load, and audit the same
  state every time.
- **Guided memory hygiene:** explicit memory rules and exact audit reports give
  the agent a repeatable way to organize and prune what it stores.
- **Bring your own agent:** Agent Builder gives it any name, voice, and mission
  while the mechanism underneath stays constant.
- **Expandable:** add new capabilities and dispatch to specialist skills without
  touching the tested core.

This repository ships the generic, tested parts of the system: deterministic
sanctum initialization and waking, memory curation guidance and auditing, First
Breath, Remember, Recall, templates, and tests. BMad Agent Builder creates the
owner-specific identity seed and behavior. First Breath personalizes the
sanctum. The optional BMad Module Builder packages and validates the finished
agent skill for installation or distribution.

The repository is named `personal-agent-template`. The skill and sanctum use
`local-agent` as their stable mechanical name. The agent's personal name, icon,
title, voice, mission, owner context, capabilities, and specialist integrations
are all customizable.

## Quick Start

You need Node.js 20.12 or newer, Git, [`uv`](https://docs.astral.sh/uv/), and an
AI tool supported by the BMad installer. Run
`npx bmad-method install --list-tools` to see the current tool list.

1. Get the template into its canonical home, then enter the folder. Clone it, or
   unzip a downloaded copy:

   ```bash
   git clone <template-repository-url> ~/local-agent
   cd ~/local-agent
   ```

2. Install BMad Builder:

   ```bash
   npx bmad-method@latest install
   ```

   In the installer, select **BMad Builder (BMB)**. Optionally add specialist
   modules the owner uses, such as **Test Architect**.

3. Launch one of the AI tools selected during installation from this folder. On
   first use, invoke:

   ```text
   bmad-bmb-setup
   ```

   Then invoke `bmad-agent-builder`, choose **Build Process (BP)**, and point it
   at `skills/local-agent`. Tell Agent Builder to preserve the existing scripts,
   tests, generic assets, and references. Customize the owner's name, voice,
   mission, and context. Point it at an existing assistant or memory export when
   continuity should carry forward. The [Full Guide](docs/full-guide.md) provides
   a complete prompt for this step.

4. Install the generated skill into the selected AI tools. Run the installer
   again:

   ```bash
   npx bmad-method@latest install
   ```

   Choose **Modify Install**, keep the existing module and tool selections, then
   add `~/local-agent/skills` when prompted for a custom source. The installer
   discovers `local-agent` after Agent Builder creates its `SKILL.md`. See the
   [custom-module installation guide](https://docs.bmad-method.org/how-to/install-custom-modules/)
   for local and non-interactive alternatives.

5. Invoke the generated skill from `~/local-agent`. Its first activation routes
   to First Breath, scaffolds `_bmad/memory/local-agent/`, and personalizes the
   identity and owner context. First Breath writes `.born` after the core memory
   is complete. Future activations load the saved sanctum.

The [Full Guide](docs/full-guide.md) covers verification, migration from an
existing assistant, Module Builder packaging, First Breath, moving to another
device, and safe BMad updates.

## Quick Answers

### Why use local files instead of hosted assistant memory?

Plain Markdown gives the owner inspectable state, version history, portability,
and direct control over retention. The same approved files can support multiple
tools that can load the local skill and read its files.

Local storage does not guarantee that content stays on the device while an AI
tool uses it. The selected tool and model provider may receive context read from
these files. Review their data handling, follow organizational policy, and keep
credentials out of the sanctum.

### Is it safe?

The design is transparent and auditable. It is not a security boundary. File
permissions, Git remotes, device security, model-provider terms, tool access,
and the owner's review discipline determine the real security posture.

### What does it cost?

The template uses plain files and open tooling. AI tools, model usage, private
Git hosting, and optional specialist modules may have their own costs. The
design is reversible because the durable state remains ordinary files.

### Why ship a template?

Lifecycle code should be deterministic. Recreating wake, initialization, and
curation scripts during every Agent Builder run can revive bugs that were
already solved. This template preserves the tested mechanism while leaving
character and owner context open for customization.

## What Ships

```text
skills/local-agent/
├── assets/
│   ├── BOND-template.md
│   ├── CREED-template.md
│   ├── INDEX-template.md
│   ├── MEMORY-template.md
│   └── PERSONA-template.md
├── examples/
│   └── external-skill-dispatch.md
├── references/
│   ├── capability-authoring.md
│   ├── first-breath.md
│   ├── memory-guidance.md
│   ├── prompt-quality-canon.md
│   ├── recall.md
│   └── remember.md
└── scripts/
    ├── curate.py
    ├── init-sanctum.py
    ├── wake.py
    └── tests/
        ├── test_curate.py
        ├── test_init_sanctum.py
        ├── test_lifecycle.py
        └── test_wake.py
```

The template intentionally omits these owner-specific or generated files:

- `skills/local-agent/SKILL.md`
- `skills/local-agent/customize.toml`, which memory agents disable by default
- `_bmad/` installer output
- `_bmad/memory/local-agent/` runtime memory
- prior assistant exports and raw imports

Agent Builder creates the skill entry and identity seed. First Breath creates
the sanctum, which is the primary customization surface for a memory agent. The
BMad installer creates project-managed configuration. A `customize.toml` surface
is appropriate only for a narrow requirement that the sanctum cannot express.

## What Triggers the Scripts

No background process runs the scripts or watches the filesystem. The generated
`SKILL.md` and its references tell the active AI tool when to invoke them with
`uv`. `SKILL.md` is the entry point that this template deliberately omits.

**How `SKILL.md` gets created.** `bmad-agent-builder` writes it (Quick Start
Step 3, Full Guide Step 5). Agent Builder wires an "On Activation" step into the
generated `SKILL.md` that runs `uv run scripts/wake.py {project-root}` as the
agent's first act. Until Agent Builder runs, there is no `SKILL.md`, so the
template has no entry point and cannot self-activate. The scripts sit inert with
nothing to call them.

**How a command triggers it.** Once `SKILL.md` exists, a compatible tool
discovers it by its frontmatter (name and description). Invoking the skill runs
its "On Activation" step. Invocation depends on the tool. It may use a slash
command named after the mechanical skill, such as `/local-agent`, an @-mention,
or automatic loading when the tool judges the skill relevant. That single
invocation runs `wake.py`, whose output drives everything downstream:

```text
tool discovers SKILL.md (by frontmatter)
└── you invoke the skill  (e.g. /local-agent)
    └── SKILL.md "On Activation" → uv run scripts/wake.py {project-root}
        ├── FIRST_BREATH        → references/first-breath.md → init-sanctum.py
        ├── FIRST_BREATH_RESUME → references/first-breath.md → continue saved work
        └── WAKING             → identity loads
memory upkeep (later, same pattern)
└── references/memory-guidance.md → curate.py
```

The scripts are deterministic tools; `SKILL.md` is what knows to call them, and
Agent Builder is what writes `SKILL.md`.

## Lifecycle

Three scripts run the mechanism. Each has a single job, and their
responsibilities never overlap:

| Script            | Role    | Runs               | Effect                |
| ----------------- | ------- | ------------------ | --------------------- |
| `init-sanctum.py` | Builder | First Breath, once | Scaffolds the sanctum |
| `wake.py`         | Router  | Every activation   | Reads only            |
| `curate.py`       | Auditor | During curation    | Reads only            |

**`init-sanctum.py` builds the sanctum.** It creates the folder structure,
copies the templates with config values substituted, copies references and
supporting scripts in, and auto-generates `CAPABILITIES.md` from capability
frontmatter. It is idempotent: if a sanctum already exists, it exits without
touching it. This one run is the only time a script writes the sanctum;
everything after is the agent editing its own memory.

**`wake.py` routes every activation.** It inspects the canonical home and picks
one mode from filesystem state alone:

```text
Activation
└── wake.py (canonical home, read-only)
    ├── no scaffold
    │   └── FIRST_BREATH
    │       └── load references/first-breath.md
    ├── scaffold, no .born
    │   └── FIRST_BREATH_RESUME
    │       └── resume First Breath
    └── scaffold + .born
        └── WAKING
            └── print INDEX, PERSONA, CREED, BOND, MEMORY, CAPABILITIES
```

`.born` is the handshake between the builder and the router. `init-sanctum.py`
lays down placeholder files but never writes `.born`; the conversational First
Breath writes it last, once the identity is real. A scaffold without `.born`
therefore means a birth was interrupted, so `wake.py` resumes it rather than
greeting over placeholders.

**`curate.py` audits the sanctum.** It reads the same canonical home and reports
the exact numbers the agent cannot eyeball: `MEMORY.md` token count against its
guardrail, session logs aged past the retention threshold, and files on disk
that have drifted out of `INDEX.md`. It never edits anything; the pruning
judgment stays with the agent, which acts on the report.

### The canonical home ties them together

`wake.py` and `curate.py` resolve one fixed sanctum home and ignore the
invocation directory, so an unrelated project can never trigger a false First
Breath or spawn a second identity. The home defaults to `~/local-agent`; set
`LOCAL_AGENT_HOME` when the repository lives elsewhere:

```bash
export LOCAL_AGENT_HOME=/absolute/path/to/local-agent
```

The `project-root` argument is situational context for the agent and never
relocates the sanctum for those two scripts. It matters mechanically in exactly
one place: `init-sanctum.py` scaffolds under `<project-root>/_bmad`, so First
Breath must run with the canonical home as its project root. That is how the
scaffold lands where `wake.py` and `curate.py` will later look for it.

## Full Guide

The [Full Guide](docs/full-guide.md) covers the deep operational path:

1. Prerequisites
2. Create the owner's repository
3. Install BMad Method and BMad Builder
4. Choose a customization path (start fresh, extend an existing assistant, or
   plan a migration with Module Builder)
5. Customize with Agent Builder
6. Package the customization with Module Builder
7. Run the lifecycle tests
8. Run First Breath
9. Import approved prior memory
10. Add an optional specialist
11. Verify end-to-end behavior
12. Daily memory discipline
13. Move to another device
14. Update BMad safely

It also carries the [Definition of Done](docs/full-guide.md#definition-of-done)
and [official BMad references](docs/full-guide.md#official-references).
