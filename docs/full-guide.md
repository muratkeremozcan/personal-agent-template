# Full Guide

The [README](../README.md) covers the common path end to end: Quick Start, what
ships, what triggers the scripts, and the lifecycle. This guide covers the same
ground in full depth: verification, importing an existing assistant's memory,
moving to another device, and updating BMad safely.

## 1. Prerequisites

Install:

- Node.js 20.12 or newer
- Git
- `uv` for the Python scripts
- A supported skill-capable AI coding tool

Verify the local tools:

```bash
node --version
git --version
uv --version
```

## 2. Create the Owner's Repository

Clone or copy this template into its canonical home:

```bash
git clone <template-repository-url> ~/local-agent
cd ~/local-agent
```

Use a private remote when the finished repository will contain personal memory.
Local Git without a remote is also valid.

Review `.gitignore` before importing anything. The main sanctum is intentionally
tracked so Git can provide history and rollback. The optional
`knowledge/private/` path is ignored as an owner-maintained convention.

## 3. Install BMad Method and BMad Builder

From `~/local-agent`:

```bash
npx bmad-method@latest install
```

In the interactive installer:

1. Select BMad Method and BMad Builder.
2. Select only the AI tools the owner uses.
3. Keep the installation project-scoped inside `~/local-agent`.
4. Add an optional specialist module only when its domain and data policy fit
   the owner.

On first use, invoke:

```text
bmad-bmb-setup
```

## 4. Choose a Customization Path

### Start Fresh

Let Agent Builder create an identity seed. First Breath will discover the
agent's character, mission, owner relationship, and working style.

### Extend an Existing Assistant

Place exports or source files under `imports/raw/`, which is ignored by default.
Treat every source as read-only during classification.

Create an inventory before Agent Builder writes anything:

| Source                                | Destination                     |
| ------------------------------------- | ------------------------------- |
| Assistant identity and voice          | `SKILL.md` and persona template |
| Mission, values, and boundaries       | Creed template                  |
| Confirmed owner facts and preferences | `BOND.md` after review          |
| Durable decisions and open threads    | `MEMORY.md` after review        |
| Repeatable abilities                  | References and registration     |
| Detailed reusable knowledge           | Indexed knowledge files         |
| Stale, duplicate, or unverified data  | Correct, quarantine, or discard |
| Credentials and secrets               | Never import                    |

An existing persona can seed the new agent. Existing memories require separate
review because they may contain stale facts, private data, or assumptions that
the owner never approved as durable memory.

### Plan a Migration with Module Builder

For a substantial existing assistant or memory archive, invoke
`bmad-module-builder`, choose Ideate Module, and provide this prompt:

```text
Plan a customized standalone local memory agent from the generic template at:
[absolute-repository-path]/skills/local-agent

Existing assistant or memory source:
[read-only path]

The finished plan must separate:
- owner-specific identity and behavior for Agent Builder
- reviewed runtime memories for the sanctum
- reusable capabilities for references and registration
- optional installed specialist modules for dispatch by reference
- stale, duplicate, unverified, sensitive, and prohibited content

Preserve the existing lifecycle scripts, tests, generic templates, and references.
Do not package raw exports or runtime memory. Propose a review checkpoint before
any prior memory is written. Keep credentials and secrets excluded.

The output must give Agent Builder enough context to create SKILL.md and the
owner-specific seed. It must also give Create Module enough context to package
and validate the finished standalone skill.
```

Pass the resulting plan to Agent Builder in step 5 and back to Module Builder in
step 6. The plan classifies proposed customization. It does not authorize writes
to the sanctum.

## 5. Customize with Agent Builder

Invoke `bmad-agent-builder` and choose Build Process. Include the optional
Module Builder plan when one exists. Give it the following prompt after
replacing every bracketed field:

```text
Customize the local memory agent template at:
[absolute-repository-path]/skills/local-agent

Owner-specific inputs:
- Agent display name: [name or discover during First Breath]
- Pronouns: [pronouns]
- Initial role and relationship: [description]
- Existing assistant or memory source: [read-only path or none]
- Optional installed specialist skills: [skill names and domains or none]

The mechanical skill name remains local-agent. The display identity is fully
owner-specific.

Preserve the existing lifecycle mechanism:
- scripts/init-sanctum.py
- scripts/wake.py
- scripts/curate.py
- scripts/tests/
- generic assets and references already present

Do not regenerate, replace, or relocate the lifecycle scripts. They are tested.
Wire them into the generated SKILL.md:
- On every activation, run wake.py with the active project root.
- FIRST_BREATH loads references/first-breath.md.
- FIRST_BREATH_RESUME loads references/first-breath.md and preserves partial state.
- WAKING uses the identity bundle emitted by wake.py.
- Memory curation runs curate.py for exact metrics before any model-led edits.

Create only the owner-specific agent layer:
- SKILL.md with identity seed, activation routing, memory discipline, and boundaries
- Owner-approved identity values in PERSONA-template.md
- Owner-specific creed seeds when they are already known
- Optional external specialist dispatch references
- Any capability that is truly specific to this owner

Use the sanctum as the primary customization surface. Keep customize.toml
disabled by default. Add it only when the owner has a
narrow pre-sanctum organizational requirement that PERSONA, CREED, BOND, MEMORY,
CAPABILITIES, and indexed knowledge cannot express. Document and test any such
exception.

The agent type is memory. Capabilities are evolvable. Identity and memory stay
local. The agent must never fabricate stored memory or claim an optional skill
is installed without verification.

If an existing assistant source is provided, read it as migration evidence.
First inventory identity, instructions, memories, capabilities, stale claims,
duplication, and sensitive material. Show the proposed mapping before writing.
Preserve the owner's approved voice and useful continuity. Do not bulk-copy raw
history into MEMORY.md. Never ingest credentials or secrets.

Keep MEMORY.md concise. Put detailed approved knowledge in indexed organic files.
Record personal runtime memory under _bmad/memory/local-agent after First Breath,
outside the distributable skill module.
```

Review the output. Confirm that Agent Builder created `SKILL.md`, retained every
lifecycle script and test, resolved intended identity placeholders, and left
unapproved memories untouched.

Run Agent Builder Quality Optimize. Resolve every valid finding, then run the
tests in step 7.

## 6. Package the Customization with Module Builder

Agent Builder owns agent behavior. Module Builder owns installable packaging,
registration, configuration variables, help entries, and structural validation.

Invoke `bmad-module-builder`, select Create Module, and provide this prompt:

```text
Package the customized standalone agent skill at:
[absolute-repository-path]/skills/local-agent

Read the finished SKILL.md and preserve the existing owner-approved identity,
behavior, capabilities, lifecycle scripts, tests, templates, and references.
Treat this as a standalone self-registering module unless the folder now contains
multiple independent skills.

Customization requirements:
- Keep the personal display identity separate from the mechanical local-agent code.
- Treat the sanctum as the primary owner customization surface.
- Capture only small shared project settings as module configuration.
- Do not create customize.toml unless an explicit pre-sanctum organizational
  requirement cannot live in the sanctum. Document and test that exception.
- Keep per-owner preferences and memory in the sanctum.
- Keep _bmad/memory/local-agent and imports/raw outside the module package.
- Register each real capability with accurate help text and a unique menu code.
- Declare every optional external skill as a dependency or documented integration.
- Preserve the owner's approved migration from an earlier assistant without
  packaging raw exports or private runtime memory.
- Preserve all existing lifecycle files. Do not regenerate their implementation.

Generate the standalone registration assets and marketplace metadata supported by
the current BMad Builder. Then run Validate Module and resolve structural, help,
reference, dependency, and description findings until validation is clean.
```

Suggested module identity:

```text
Name: [owner-selected module name]
Code: local-agent
Description: [one precise sentence describing this owner's agent]
Version: 0.1.0
```

Module Builder may add registration assets and merge scripts. Review those files
before committing. It must not add sanctum data or raw imports to the module.

## 7. Run the Lifecycle Tests

From the repository root:

```bash
uv run skills/local-agent/scripts/tests/test_wake.py
uv run skills/local-agent/scripts/tests/test_curate.py
uv run skills/local-agent/scripts/tests/test_init_sanctum.py
uv run skills/local-agent/scripts/tests/test_lifecycle.py
```

The suite verifies:

- First Breath, interrupted First Breath, and normal waking modes
- canonical-home behavior from an unrelated project directory
- token guardrails, stale session detection, and index drift
- deterministic scaffolding and idempotency
- generic Remember and Recall discovery
- external capability source formatting

## 8. Run First Breath

Activate the generated agent with `~/local-agent` as its canonical home. The
generated SKILL.md should run the initializer and route into
`references/first-breath.md`.

First Breath should establish:

- confirmed identity and communication style
- a specific mission for this owner
- owner context and working preferences
- memory and privacy boundaries
- installed tools and optional specialist dispatch
- corrections to any approved seed from a previous assistant

The conversation writes throughout the session. It writes `.born` last. If the
session ends early, the next activation resumes from the partial sanctum.

After First Breath, review before committing:

```bash
git status
git diff
git add <reviewed-files>
git diff --cached
git commit -m "Complete First Breath"
```

Never stage raw imports casually.

## 9. Import Approved Prior Memory

Migrate one document or one coherent batch at a time. Use this review prompt:

```text
Review this source as possible continuity for my local agent.

Separate identity, instructions, owner facts, preferences, beliefs, decisions,
capabilities, examples, stale claims, duplication, and sensitive material.
Do not write yet.

For each retained item, propose one destination: PERSONA, CREED, BOND, MEMORY,
CAPABILITIES, an indexed knowledge file, a device-local private knowledge file,
or discard. Flag ambiguity and conflicts for my decision. Identify credentials
and secrets for exclusion without reproducing their values.

After I approve the map, write concise content, preserve source name and import
date where provenance matters, update INDEX for every organic file, and replace
stale facts in place.
```

Avoid loading an entire archive into `MEMORY.md`. Every token in that file loads
on every waking. Keep raw history ignored, distill durable knowledge, and put
detail in indexed files.

## 10. Add an Optional Specialist

`skills/local-agent/examples/external-skill-dispatch.md` is a generic starting
point. Replace every placeholder, verify the specialist is installed, and move a
customized copy into `skills/local-agent/references/` before First Breath.

Agent Builder should update SKILL.md and the capability description. The init
script discovers external capability frontmatter and records the installed skill
by reference. The specialist keeps ownership of its domain implementation.

If the sanctum already exists, add the dispatch as a learned capability within
the sanctum and register it in CAPABILITIES.md and INDEX.md. The initializer is
idempotent and will not overwrite an existing sanctum.

## 11. Verify End-to-End Behavior

Use separate sessions for these checks:

1. Ask the agent to remember a harmless preference.
2. End the session and start another from an unrelated project.
3. Ask it to recall the preference and identify the source file.
4. Correct the preference.
5. Start another session and confirm the old value was replaced.
6. Interrupt a disposable First Breath before `.born`, then confirm the next
   activation resumes it.
7. Create a disposable unindexed organic file, run curation, and confirm index
   drift reports it.
8. If prior memories were imported, sample facts against their source and check
   that stale or rejected content is absent.
9. If a specialist was attached, trigger a request inside its domain and confirm
   direct dispatch.

## 12. Daily Memory Discipline

- Record explicit remember requests immediately.
- Append meaningful session outcomes to `sessions/YYYY-MM-DD.md`.
- Keep MEMORY.md distilled and current.
- Move detailed material into indexed knowledge files.
- Update INDEX.md whenever an organic file is created.
- Run `curate.py` periodically and make pruning decisions from its report.
- Correct stale facts in place.
- Review every Git diff before committing memory.

## 13. Move to Another Device

Clone the private repository or transfer it through an approved encrypted
channel. Reinstall BMad and the selected tool integrations on the new device:

```bash
cd ~/local-agent
npx bmad-method@latest install
```

Anything ignored by Git will require a separate, deliberate transfer. Verify
Recall on harmless content before relying on the new device.

## 14. Update BMad Safely

Create a reviewed checkpoint before updating:

```bash
git status
git add <reviewed-files>
git commit -m "Checkpoint before BMad update"
npx bmad-method@latest install
```

Review the installer diff. Keep durable customization in supported BMad
configuration surfaces, the skill source, and the sanctum. Avoid long-term edits
to installer-managed generated files.

## Definition of Done

The agent is ready when:

- Agent Builder Quality Optimize is clean.
- All lifecycle tests pass.
- Module Builder validation is clean.
- First Breath completed and `.born` exists.
- Identity placeholders are resolved.
- Remember, Recall, correction, resume, and curation work across sessions.
- Approved prior memories have provenance and rejected content is absent.
- No credentials or raw imports are tracked.
- Optional specialists are installed, registered, and dispatch correctly.
- The repository has a reviewed commit.
- A second supported tool can load the same agent and sanctum.

## Official References

- [BMad installation](https://docs.bmad-method.org/how-to/install-bmad/)
- [BMad Builder quick start](https://bmad-builder-docs.bmad-method.org/)
- [Build a module](https://bmad-builder-docs.bmad-method.org/tutorials/build-your-first-module/)
- [Builder command reference](https://bmad-builder-docs.bmad-method.org/reference/builder-commands/)
- [Agent memory and personalization](https://bmad-builder-docs.bmad-method.org/explanation/agent-memory-and-personalization/)
- [Skill customization guidance](https://bmad-builder-docs.bmad-method.org/how-to/make-a-skill-customizable/)
- [Module configuration](https://bmad-builder-docs.bmad-method.org/explanation/module-configuration/)
- [Custom module installation](https://docs.bmad-method.org/how-to/install-custom-modules/)
- [Module distribution](https://bmad-builder-docs.bmad-method.org/how-to/distribute-your-module/)
