---
name: Archive
description: Move an aged session log out of the sanctum into the archive, through the redaction gate
code: ARC
added: 2026-08-27
type: prompt
---

# Archive

The outcome is a session log that has left the sanctum and is still findable: a note in
`<archive>/log/YYYY/MM/` carrying frontmatter that wires it into the graph, an INDEX.md entry that
points at the new location, and every confidential line still sitting inside the sanctum where it
started. The consumer is future-you a year from now reconstructing what happened, plus any graph-aware reader's
graph, which can only draw the edges the frontmatter and the Linked block hand it.

`references/curation-pass.md` already states the principle this capability implements: "Lean means
relocated, never deleted." Until now the sanctum had nowhere to relocate a session log to, so Phase
4 deleted it. The archive has no token budget and unlimited retention, so it is the destination that
rule was always missing.

## When it runs

During a curation pass, on the logs `scripts/curate.py` reports under `session_logs.stale`. Those
are the files whose filename date is past the retention threshold, First Breath excluded (the
exemption lives in `stale_logs()` in `scripts/_sanctum.py` and reads the `.born` marker, so it holds
whatever date the sanctum was born on).

the owner can also invoke it directly: "archive that log", "archive everything before August", "put the
last quarter's planning notes in the archive." Direct invocation runs the same gate in the same order. Nothing about
being asked by name relaxes the redaction rules.

The First Breath log is never archived and never pruned. It stays in `sessions/` because the record
of being born is continuity itself.

`BOND.md`, `PERSONA.md`, `CREED.md` and anything under `notes/` are outside this capability's reach
unless the owner names the file. The repository `.gitignore` carries a standing warning that personal
facts in BOND.md must never leave the machine; archiving is exactly the mechanism that would move
them.

## Where it lands

`<archive>/log/YYYY/MM/<slug>.md`, resolved against the archive root at `<archive>/`.

Take the year and month from the date inside the source filename, using the same date-in-filename
rule `curate.py` uses to decide the log is aged. Reading the date from one place is what keeps the
aged list and the archive location from ever disagreeing about which month a log belongs to.

The slug is `YYYY-MM-DD-<topic>`, which is the sanctum filename unchanged when the log already
carries a topic suffix. `sessions/YYYY-MM-DD-example-topic.md` becomes
`<archive>/log/2026/05/2026-05-04-example-topic.md`. A bare day log such as
`sessions/2026-08-19.md` has no topic, so derive one from the log's own H1 or its first
`## Session — ...` line: lowercase, hyphenated, six words at most. Keeping the date inside the slug
makes the note sort correctly in a flat search and keeps two same-topic logs from different months
apart.

### The filename is taint too

The slug, the `source` frontmatter value, and the path printed inside the withheld notice are three
verbatim copies of the sanctum filename, and none of them passes through the body gate. A log named
`sessions/YYYY-MM-DD-person-departure.md` therefore announces its own subject from the archive path,
the frontmatter and the notice, three times over, while the body shows nothing at all. This is not
hypothetical: real sanctum filenames already carry colleagues' names, and
`sessions/YYYY-MM-DD-colleague-name-topic.md` is one of them.

Run the filename-derived slug through the same personnel and marker checks the body gets, before
anything is written. When it passes, proceed as above. When it fails:

- Re-slug from the redacted content: keep the date, and take the topic from a term that survived
  redaction. `YYYY-MM-DD-person-departure` becomes `2026-08-01-team-change` or, when nothing
  survives that describes the log, `2026-08-01-withheld`.
- Set `source_withheld: true` in the frontmatter and omit `source` entirely. Provenance still exists
  in `sessions/redacted/`, which never leaves the machine.
- Print the category and the date in the withheld notice, never the original path.
- Say so in the report. A renamed slug is something the owner should see, because it means a filename they
  chose was carrying something it should not have.

The same check applies to any H1 or `## Session — ...` line used to derive a topic for a bare day
log. A heading is as public as a filename once it becomes the slug.

If the target file already exists and its `source` frontmatter already lists this sanctum filename,
the log is archived and there is nothing to do. If the target exists from a different source, append
the new content under a `## Archived from <filename>` heading and merge the frontmatter lists.
Overwriting an archive note is how five years of delivery record gets quietly destroyed.

## The archived note

Preserve the log verbatim, minus what the gate withholds. No rewriting, no summarizing, no tidying
the prose. MEMORY.md already holds the distillation; the archive holds the record, and a record that
was improved on the way in is worth less than the one that arrived intact.

The frontmatter:

Every example below is synthetic. The one real marked log in the sanctum makes a poor worked
example precisely because the people named in its withheld block are the ones that must not reach
the frontmatter, so using it here would print them into this file.

```yaml
---
type: session-log
date: 2026-05-04
source: sessions/2026-05-04-example-topic.md
people: ["[[person/Example]]"]
themes: ["[[theme/example-theme]]", "[[theme/other-theme]]"]
repos: ["[[repo/example-service]]", "[[repo/example-toolkit]]"]
redacted: true
redacted_count: 1
---
```

Read that `people` list against `redacted_count: 1`. The withheld block in this fixture named a
second person, and that is exactly why only one appears. Had the list carried both, the frontmatter
would have announced who the withheld block was about while the body showed nothing.

`date` is the log's date. `source` is the original sanctum path, so provenance survives even if the
slug is later renamed. The three link lists are quoted wikilinks, which is the form Obsidian and compatible readers
require for internal links inside list properties.

Fill the link lists from the deployment's shared taxonomy file if it has one. This template ships
no such file, because what counts as a person, a theme or a repository is deployment-specific.
Without one, derive the lists from the entity notes that already exist in the archive and from the
names appearing in the log itself. Either way the shape is the same:

- **people** — the keys under `people`, matched on the key or any of its `aliases`. Link target is
  `person/<Name>`, using the taxonomy key's exact capitalisation so every spelling and nickname of one person
  lands on one node.
- **themes** — the keys under `themes`, matched by that entry's own `patterns` regexes against the
  archived text. Link target is `theme/<slug>`, the taxonomy key verbatim.
- **repos** — the repository name from any GitHub URL in the body, or from a bare repo name that
  matches one already carrying an entity stub. Link target is `repo/<name>`, **except for any name
  listed under `repo_collisions`, which takes `repo/<org>-<name>`**. Three names exist under two
  orgs each, so a bare `[[repo/authentication-service]]` resolves to nothing and merges two
  different repositories into one phantom node. Both archive generators already read that key; Archive
  reads the same key or it silently disagrees with them.

A name that is absent from the taxonomy stays in the prose and out of the frontmatter. An unresolved
`[[person/Someone]]` puts a person on the graph that the archive cannot explain, which is a disclosure.
Issue-tracker keys are the deliberate exception: they stay unresolved on
purpose and cost no files.

Compute all three lists from the redacted text, after the gate has run. A person named only inside a
withheld block must never appear in `people:`, because the graph would then advertise the existence
and subject of the thing that was withheld. This is the subtlest leak this capability can produce
and it is invisible in the note body.

Append the guarded block so the edges exist regardless of how the build treats frontmatter links:

```markdown
<!-- archive-linkify:start -->
## Linked
[[person/Example]] · [[theme/example-theme]] · [[repo/example-service]]
<!-- archive-linkify:end -->
```

The block carries exactly the same entities as the frontmatter, computed from the same redacted
text. Any entity that reaches one and not the other is a bug, and the read-back in step 5 checks it.

If the deployment runs a separate tool that maintains these blocks across the archive, check
whether it excludes `log/` from its scan; the reference implementation does. Where it is excluded,
Archive owns these blocks for their whole life and must write them correctly on the first pass,
because re-running that tool over the archive is not an available repair.

## INDEX.md

Rewrite the entry, keeping its hook. An archived log that vanishes from the index is a lost file,
which is the failure INDEX.md exists to prevent.

The existing entry for a real log reads:

```markdown
  - `sessions/2026-05-04-example-topic.md`: the review comments on the draft
    mapped to fixes, the vendor pricing research, and the open items only the
    owner can answer.
```

After archiving it reads:

```markdown
  - `<archive>/log/2026/05/2026-05-04-example-topic.md`: archived. Review comments
    mapped to fixes, vendor pricing, and the items only the owner can answer.
    One block withheld, in `sessions/redacted/`.
```

Keep the pointer inside backticks. `prose_of()` in `scripts/_sanctum.py` strips backticked spans
before measuring an entry against the 150-character target, so a long archive path costs nothing
against the index budget while the prose stays inside it.

When a whole month goes to the archive at once, collapse the entries into one cluster line for
`<archive>/log/YYYY/MM/` naming the topics, which is what Phase 4 of the curation pass already asks for.
Add one line for `sessions/redacted/` the first time that folder appears.

Make these as targeted edits against anchored text. Parallel instances share INDEX.md and a
wholesale rewrite drops whatever another instance just wrote.

## The redaction gate

An archive is normally git-backed, and may be copied again by a sync service or a remote.
Anything this capability moves may therefore be replicated to further durable stores, some of them
off this machine. The gate is what stands between the sanctum's confidential material and those
copies, and it runs on every archive, including the ones the owner asks for by name.

### 1. Confidentiality markers

A marker is a labelled prefix at the start of a line. Strip leading whitespace, blockquote `>`
characters, list bullets (`-`, `*`, `+`, or `1.`), and markdown emphasis characters, then look at the
first bolded span or the first clause up to a colon. It is a marker when that span contains any of
these tokens, matched case-insensitively:

`confidential` · `never repeat` · `do not repeat` · `do not share` · `not for sharing` ·
`do not put in` · `do not surface` · `never surface` · `off the record` · `keep this between` ·
`keep it confidential` · `in confidence` · `private` · `sensitive` · `internal only` · `nda` ·
`under embargo` · `unannounced` · `between us` · `stays between` · `don't tell` · `do not tell` ·
`keep this quiet` · `not to be shared` · `off books`

**This list is illustrative and never exhaustive.** Any phrasing that implies the writer expected the
material to stay put counts as a marker, whether or not its words appear above. "A colleague said this
stays between us: the second office may close" carries no token from an earlier version of this
list, names no individual against a personnel topic, and is no credential. It is still marked. When a
line reads as something someone asked to be kept quiet, treat it as marked and move to fail closed.

A sanctum in real use accumulates marked blocks. **No real one is reproduced in this file.** A
specification for a confidentiality gate is a repo file that gets committed, synced and read by
agents, so quoting the secret into it defeats the gate at the only point where the gate is being
defined. Read the live instance at `sessions/YYYY-MM-DD-example-topic.md:3-6` when you
need to test against the real thing, and keep it there.

The structural facts about it, which are what the gate needs, are safe to state: it is a bolded
prefix marker on the first line of a four-line contiguous paragraph, it carries three separate
marker signals (the prefix, a mid-sentence request to keep something confidential, and a trailing
instruction never to surface it), and its topic is personnel.

Work from this synthetic fixture instead, which has the same shape and no real content:

```markdown
**Confidential, do not put in any shared doc/deck/Slack:** [Person A] told [Person B] in this
meeting that [Person C] is leaving, and asked them to keep it confidential. [Person A] plans to
reach out personally afterwards. Noted here only for continuity; never surface in the pre-read,
deck, or any message to a third party.
```

Note that no two sources describe the real block the same way. Earlier drafts of
In one real deployment, two separate documents each cited a marked block as reading
"Confidential, never repeat", a paraphrase of a string that exists nowhere on disk. Match on tokens
for exactly this reason. A gate built to match the quoted phrase would have let the only real
instance in the sanctum through untouched.

Also treat a block as marked when a marker phrase appears mid-sentence inside it: "asked them to keep
it confidential", "never surface in", "he told me privately", "this stays between us", "don't tell
anyone", "keep this quiet", "not to be shared". The real example carries three such phrases in four
lines. These are examples of a shape rather than a lookup table; match the intent.

**What gets withheld is the whole block**, defined by where the marker sits:

- A marker in a paragraph withholds that paragraph: every contiguous non-blank line up to the next
  blank line. In the real example that is all four lines, opening block through "third party."
- **A marker line carrying no content of its own withholds down to the next heading**, the same scope
  the heading rule uses. This is the shape that defeats a plain paragraph rule:

  ```markdown
  **Confidential:**

  The second office lease will not be renewed.
  ```

  The paragraph holding the marker contains only the label, so withholding that paragraph withholds
  nothing, and the secret sits in the next paragraph carrying no marker of its own. Where the secret
  is neither personnel nor a credential, an unannounced org change, a deal, a strategy decision, no
  other rule catches it either and it archives to a git-backed, possibly replicated store.

  Test for it by stripping the marker span from its line. When what remains is empty, or punctuation
  and nothing else, the marker is a label for what follows rather than a prefix to its own sentence,
  so extend the scope to the next heading.
- A marker in a list item withholds that item, its indented continuation lines, and everything
  nested under it.
- A marker on a heading, or on the first line under a heading, withholds the entire section: the
  heading and everything down to the next heading of the same or higher level.
- A marker anywhere in a table withholds the whole table. A single redacted row leaves a table whose
  shape still describes the thing that was removed.

Withhold the surrounding sentence too when a later paragraph refers back to the marked one ("as
above", "per the note at the top"). A dangling reference to removed content tells the reader what
category of thing was removed and often who it concerned.

### 2. Named personnel material, withheld by default

Withhold any block that names an individual and touches one of these, whether or not it carries a
marker:

termination, firing, layoff, redundancy, resignation, being let go · performance ratings, reviews,
PIPs, or an assessment of someone's competence · compensation in any form: salary, bonus, equity,
band, level, raise · hiring and firing decisions, candidate assessments, interview debriefs ·
disciplinary action or a complaint · health, medical, family or immigration circumstances.

A named individual means any person's proper name, which includes every key and alias under `people`
in the deployment's taxonomy file, if it has one, plus any other given name or full name appearing in the log. Company
names, product names and team names are not people.

The gate protects third parties. the owner's own level, compensation, promotion case and job-security
thread are the owner's own record in their own archive, and they archive normally.

Org-level facts with no individual attached archive normally too: "the TA role is being phased out
org-wide, no new hires" is a structural fact about the organisation. The moment a name attaches to
it, the block is withheld.

### 3. The visible trace

Every omission leaves a marker in the archived file, at the position the content was removed, in
document order. A silent hole is the failure mode this rule exists to prevent: a reader who cannot
see that something is missing will conclude the record is complete.

```markdown
> [!warning] Withheld from archive
> 1 block withheld: personnel. Full text stays in the sanctum at
> `sessions/redacted/2026-05-04-example-topic.md`.
```

Set `redacted: true` and `redacted_count: N` in the frontmatter so a query or a grep can list every
archived note carrying an omission without opening any of them.

The category comes from this closed vocabulary, and the notice carries the category and nothing
else:

- `personnel` — termination, hiring, performance, discipline
- `compensation` — pay, equity, level, band
- `third-party-private` — health, family, immigration, personal circumstances
- `security` — credentials, tokens, keys, private endpoints
- `marked-confidential` — carried a marker, topic not otherwise classified

Never name the person and never restate the specific detail. "1 block withheld: personnel" is
legible. A notice naming the person and the event, in the shape "1 block withheld: [Person C]'s
departure", reproduces the secret inside the notice that was supposed to protect it, and does it in
the copy that syncs off the machine.

### What the automated gate cannot do

The gate matches text. It catches a withheld sentence reproduced, a long word run that
survived light rewording, a credential-shaped value, and an entity name reaching the filename
or the frontmatter. It does **not** understand meaning.

A review demonstrated the gap by archiving "Leadership plans Friday cancellation for
Nightingale; Jordan employment will end" against withheld text saying Nightingale would be
cancelled on Friday and Jordan dismissed. Every fact survived, no phrase did, and the gate
passed it.

Nothing in a text comparison closes that, so it is a standing limitation rather than a bug
awaiting a fix. Two consequences, both binding:

- **The gate is a floor, never a clearance.** A pass means no textual leak was found. It is
  not a statement that the archived note is safe to publish.
- **Rewriting a withheld block in your own words is a redaction failure**, however different
  the wording. The rule is to withhold the block, not to paraphrase it. When a summary of a
  withheld topic seems necessary, it goes in the notice as a category and nothing more.

Where semantic equivalence is uncertain, fail closed and ask the owner. That is the branch
below, and this is the case it exists for.

### 4. Fail closed

Uncertainty resolves toward the sanctum. Every branch below keeps material out of the archive and tells
the owner what happened.

- **Uncertain about a block.** Withhold it. Count it under the closest category, defaulting to
  `marked-confidential`. A block that stayed behind can be archived next week after they look at it;
  a block that reached a synced git repo cannot be recalled.
- **Uncertain about the file.** When redaction would leave a stub, or the log's subject is a
  personnel discussion end to end, do not archive it at all. Leave the file in `sessions/`, leave its
  INDEX.md entry pointing at the sanctum, and say so in one line. Never skip a file silently: a log
  that neither archived nor reported looks identical to one that was never aged.
- **Anything shaped like a credential.** Tokens, API keys, cookies, OAuth token paths, private
  endpoints, session identifiers. Withhold under `security` with no further analysis. An agent's own
  `CAPABILITIES.md` tends to accumulate exactly this, because recording how to reach a service is
  useful and recording the credential alongside it is the path of least resistance. Assume the
  material is present rather than checking, and spend one rule on it.
- **Nothing is deleted before it is shown.** Report the withheld list to the owner, by file and category,
  before any sanctum log is pruned. They are the only one who can tell you a withheld block was fine
  or a passed block was not.

### Where the withheld material goes

Withholding a block from the archive and then pruning the sanctum log destroys the block, which is a
worse outcome than the leak this gate prevents.

So when a log carries withheld content, the withheld blocks move to
`sessions/redacted/<original-filename>.md` before the source log is pruned, under a header naming the
archive note they were removed from. That subfolder is invisible to the aged-log scan, because
`stale_logs()` in `scripts/_sanctum.py` globs `sessions/*.md` without recursing, so the material
stays in the sanctum permanently and never re-enters the archive queue. Index the folder once as a
cluster line.

`sessions/redacted/` is the one place in the sanctum that must never be archived, and it never ages
out. Say that in its INDEX.md line so a future pass reads the guard before it acts.

## Order of operations

The sequence is the safety property. Run it in this order and never compress it.

1. Read the source log in full.
2. Run the gate. Produce the redacted text and the withheld blocks.
3. Check the filename. Re-slug and set `source_withheld` if it fails the gate.
4. Write `sessions/redacted/<filename>.md` if anything was withheld.
5. **Read `sessions/redacted/` back and confirm every withheld block is present in it, byte for
   byte.** Abort the whole archive on any mismatch, before anything else is written. After step 9
   this file is the only copy of that material, so a truncated or failed write here converts a
   redaction into a deletion.
6. Write the archive note: frontmatter computed from the redacted text, body, withheld notices, Linked
   block.
7. Read the archive note back and confirm the withheld strings are absent from it, and that the slug,
   `source` and notices carry nothing the gate withheld. Run
   `uv run scripts/verify_archive_redaction.py <archive-note> <redacted-file>` and require exit 0.
8. Rewrite the INDEX.md entry to point at the archive path. Report to the owner: what archived, what was
   withheld and under which category, what was held back entirely, and any slug that was renamed.
9. Only now prune the source log from `sessions/`.

Two orderings carry the safety property and neither is negotiable. **Step 5 before step 9** means a
failed redacted write never destroys the withheld material. **Step 7 before step 9** means a log is
deleted only after its archive note has been verified. A log pruned ahead of either check is gone.

## What the owner gets back

One line per archived log when the pass is quiet, in the same register as the rest of curation:
housekeeping, delivered without ceremony. Anything withheld or held back gets named explicitly,
because that is the part they have to make a decision about.
