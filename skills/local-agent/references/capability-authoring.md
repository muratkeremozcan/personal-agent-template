---
name: capability-authoring
description: How to author, register, and evolve learned capabilities
---

# Capability Authoring

When your owner wants you to learn a new ability, you create a capability
together. The mechanics are below; first, the one thing that decides whether the
capability is any good.

## Write the destination, not the route

Know your own default. Asked to author a capability, you will reach for numbered
steps, question lists, and mandatory template sections. Elaborate scaffolding
feels like diligence and reads like quality. That instinct is the central defect
to resist. A script is your imagined transcript of one good session; real
sessions diverge from it, and a capability that scripts the path spends your
future self's intelligence on compliance instead of the problem.

Write the destination instead. A capability prompt holds four things: the
**outcome** (the artifact or change that must exist when it has done its job),
the **consumer** (who must act on that outcome, and what they can or cannot be
assumed to know), the **bar** (what the consumer needs to be true of it), and
the **non-inferables**. Non-inferables include owner specifics worth pulling
from MEMORY.md and BOND.md, wiring such as paths and formats, and rules with
real consequences. Then stop. The outcome and its consumer imply the process. Do
not restate your stance. Your persona already supplies the voice and
relationship; the capability adds only what this ability needs.

A complete capability body, not an excerpt:

```text
The outcome is a pitch the owner can deliver tomorrow: claims they can
defend, one through-line, no slide that exists out of fear. You are
stress-testing the argument, not polishing words: wordsmithing comes
last. Push where it is weak: the number that will not survive a
question, the benefit with no evidence, the ask that got buried.
Check MEMORY.md for what this owner's audiences have punished before.
```

A scripted version would add a pitch-structure walkthrough, a ten-question
intake, and a slide template. Each addition subtracts adaptivity. The owner who
arrives with a finished deck gets pressure-testing instead of an intake
interview precisely because nothing scripted the opening.

This section is the working standard, synced from the prompt-quality canon. Load
`references/prompt-quality-canon.md` for the cut tests, the two-version
comparison, and the retirement test.

## Capability Types

A capability can take several forms.

### Prompt (default)

A markdown file with guidance on what to achieve. Best for judgment-based tasks
where you need flexibility.

```text
capabilities/
└── {example-capability}.md
```

### Script

A Python or bash script for deterministic tasks such as calculations, file
processing, data transformation, or API calls. Create the script alongside a
short markdown file that says when to run it and what to do with the results.

```text
capabilities/
├── {example-script}.md          # When to run, what to do with results
└── {example-script}.py          # The actual computation
```

Keep scripts to one job each, have them read and write within the sanctum, and
never hardcode paths: accept the sanctum path as an argument.

### Multi-file

A folder with multiple files for a more involved capability, such as a
mini-workflow with several steps plus reference material or templates.

```text
capabilities/
└── {example-complex}/
    ├── {example-complex}.md     # Main guidance
    ├── structure.md             # Reference material
    └── examples.md              # Examples for tone/format
```

### External Skill Reference

Point to an existing installed skill rather than reinventing it. If you discover
a skill that would serve your owner well, suggest it, and always ask before
installing.

```markdown
## Learned

| Code | Name       | Description  | Source                 | Added      |
| ---- | ---------- | ------------ | ---------------------- | ---------- |
| [XX] | Skill Name | What it does | External: `skill-name` | YYYY-MM-DD |
```

An optional dispatch reference uses this same pattern. It registers the domain
boundary and installed skill while leaving the specialist's implementation in
its own module.

## Prompt File Frontmatter

Every capability prompt file carries this frontmatter:

```markdown
---
name: { kebab-case-name }
description: { one line, what this does }
code: { 2-letter menu code, unique across all capabilities }
added: { YYYY-MM-DD }
type: prompt | script | multi-file | external
---
```

The body is the capability prompt itself, written to the standard above.

## Creating a Capability (The Flow)

Explore what your owner needs through conversation, then draft the capability
and refine it with them. Your persona already knows how to do that, so it needs
no script. The wiring below must happen in order because a missed registration
makes the capability invisible next session:

1. Save to `capabilities/` as a file or folder depending on type.
2. Register it in CAPABILITIES.md by adding a row to the Learned table.
3. Register it in INDEX.md by noting the new file under "My Files": an
   unregistered file is a lost file.
4. Confirm: "I'll remember how to do this next session. You can trigger it with
   [{code}]."

## Refining and Retiring

When you refine a capability after feedback, update the file in place and log
the refinement in the session log. When a capability is no longer useful, remove
its row from CAPABILITIES.md but keep the file so the owner can bring it back,
and note the retirement in the session log. Whether a capability still earns its
place is the canon's retirement test: when it stops beating what you would do
bare, retire it rather than patch it.
