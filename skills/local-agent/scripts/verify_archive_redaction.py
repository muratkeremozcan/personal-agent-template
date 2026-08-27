#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Assert that an archived note leaks nothing the redaction gate withheld.

The Archive capability (references/archive.md) decides what leaves the sanctum for the
archive, which is typically git-backed and may be replicated by a sync service. That decision is made by a
model reading a prose specification, and the same model then checks its own work. This
script exists so the safety property is executable rather than asserted: it re-reads both
files off disk and fails loudly when withheld text reached the archive.

Run it as step 7 of the archive sequence, before the source log is pruned:

    uv run scripts/verify_archive_redaction.py \\
        <archive>/log/2026/05/2026-05-04-topic.md \\
        <sanctum>/sessions/redacted/2026-05-04-topic.md

Exit 0 means every check passed. Exit 1 means a leak was found and the archive must be
rolled back. Exit 2 means the check could not run, which is also a failure: the sequence
fails closed, so an unrunnable check blocks the prune exactly like a failed one.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

# Sentences shorter than this are dropped from the sentence check. Withheld blocks contain
# ordinary connective prose ("He said that.") that legitimately recurs in the surviving text.
# Short sentences are NOT thereby unchecked: the token sweep below covers them, which is what
# closes the hole where "PersonC was fired." passed because it was 18 characters long.
MIN_SENTENCE_CHARS = 24

# Tokens this common carry no signal, so intersecting on them would flag every archive.
# Deliberately short: the cost of a false positive is a human reading one line, and the cost
# of a false negative is a secret in a synced repository that cannot be recalled.
STOPWORDS = frozenset("""
a an the and or but nor for so yet if then than that this these those there here
i me my we us our you your he him his she her it its they them their who whom whose
what which when where why how all any both each few more most other some such
no not only own same too very can will just don should now
is am are was were be been being have has had having do does did doing
of in on at by to from up down out off over under again further once
about above across after against along among around because before behind below
beneath beside between beyond during except inside into like near since through
throughout toward towards until upon with within without
as also however therefore thus while whereas although though even still yet
one two three four five six seven eight nine ten first second third next last
said says say told tell asked ask made make made makes going go goes went
get got gets getting put puts take takes taken taking come comes came
would could should might must shall may
note notes noted meeting meetings call calls team teams work works working
project projects update updates change changes changed thing things
time times day days week weeks month months year years today tomorrow yesterday
end ends ended start starts started keep keeps kept
""".split())

# Frontmatter keys whose values are structural rather than content. Their words are not
# evidence of a leak.
STRUCTURAL_KEYS = frozenset({"type", "date", "redacted", "redacted_count", "tags"})

# A digit run this long is an identifier, an amount, or a date-like figure. Exactly the
# shape of the thing worth withholding, so digits are checked rather than skipped.
MIN_DIGIT_TOKEN = 4

# A run of this many consecutive words from a withheld block appearing in the archived note
# is treated as a leak even when no whole sentence matched. Catches a rewritten sentence
# that kept the incriminating clause.
SHINGLE_WORDS = 7

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
WORD = re.compile(r"[^\W_][^\W_']*", re.UNICODE)

# Values shaped like a secret are compared verbatim with no length threshold at all.
# A review archived `PIN: 1234` past both the sentence floor and the word-run check,
# because it is neither long prose nor seven words. Length is the wrong axis for these.
CREDENTIAL_SHAPED = re.compile(
    r"""(?xi)
    (?: (?:pin|otp|code|token|key|secret|password|passcode|ssn|account|acct|iban|salary|
           severance|comp|bonus|offer|amount|rate) \s* [:=]\s* \S+ )
    | \b\d{3,}(?:[.,]\d+)?\b
    | \b[A-Za-z0-9_-]{16,}\b
    """
)

# The redacted file carries its own provenance header naming the source log. That header is
# not withheld material, so comparing a slug against it makes every archive fail on its own
# filename. Strip it before building the token set the identifier checks use.
PROVENANCE = re.compile(r"^\s*#{1,6}\s*withheld from .*$", re.I | re.M)

# Slugs and paths are hyphen-joined identifiers. Tokenizing them as prose yields one long
# token that matches nothing, which is how a filename naming the redacted subject slipped
# through the first version of this check.
IDENT_SPLIT = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    """Fold away the differences that hide a leak: case, unicode form, whitespace runs,
    and markdown emphasis. A secret retyped in bold is the same secret."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[*_`~\[\]()>#|]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    return WORD.findall(normalize(text))


def shingles(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def sentences(text: str) -> list[str]:
    out = []
    for raw in SENTENCE_SPLIT.split(text):
        cleaned = normalize(raw)
        if len(cleaned) >= MIN_SENTENCE_CHARS:
            out.append(cleaned)
    return out


# A notice a reader sees when the markdown is rendered. Callout or heading form.
VISIBLE_NOTICE = re.compile(r"withheld\s+from\s+archive", re.I)

# Constructs that render as nothing. A notice hidden in one of these is not a notice.
HIDDEN = re.compile(r"<!--.*?-->|```.*?```|~~~.*?~~~", re.S)

# Provenance keys, in any YAML spelling: bare, quoted, block scalar.
PROVENANCE_KEY = re.compile(
    r"""^\s*['"]?(source|source_path|origin)['"]?\s*:\s*(?P<v>.*)$""", re.I
)


def strip_hidden(text: str) -> str:
    """Body with HTML comments and fenced code removed.

    A review satisfied the visible-notice check with `<!-- Withheld from archive -->`,
    which renders as nothing at all. A notice that no reader sees is the silent hole the
    rule exists to prevent, so hidden constructs are removed before looking for it.
    """
    return HIDDEN.sub(" ", text)


def provenance_values(frontmatter: str) -> list[str]:
    """Every provenance value, including block scalars and quoted keys.

    A line-prefix check misses `source: >-` with the value on following lines, and
    misses a `"source"` key. Both are valid YAML and a review carried a redaction
    subject through each. Parsing without a YAML dependency means handling the shapes
    explicitly: scalar on the same line, or an indented block after `|`/`>`.
    """
    out: list[str] = []
    lines = frontmatter.splitlines()
    i = 0
    while i < len(lines):
        m = PROVENANCE_KEY.match(lines[i])
        if not m:
            i += 1
            continue
        value = m.group("v").strip()
        if value.startswith(("|", ">")):
            block, i = [], i + 1
            while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()):
                block.append(lines[i].strip())
                i += 1
            out.append(" ".join(b for b in block if b))
            continue
        out.append(value.strip("\"'"))
        i += 1
    return out


def read(path: Path, label: str) -> str:
    if not path.is_file():
        sys.stderr.write(f"cannot run: {label} does not exist at {path}\n")
        raise SystemExit(2)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"cannot run: {label} unreadable at {path}: {exc}\n")
        raise SystemExit(2) from None


def frontmatter_and_body(text: str) -> tuple[str, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return "", text
    return match.group(1), text[match.end() :]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify an archived note leaks nothing that was withheld from it."
    )
    parser.add_argument("archived_note", type=Path, help="the note written into <archive>/log/")
    parser.add_argument(
        "redacted_file",
        type=Path,
        nargs="?",
        help="the sessions/redacted/ file holding the withheld blocks; omit with --no-withheld",
    )
    parser.add_argument(
        "--no-withheld", action="store_true",
        help="verify a log from which nothing was withheld; the note must say `redacted: false`",
    )
    parser.add_argument(
        "--shingle",
        type=int,
        default=SHINGLE_WORDS,
        help=f"consecutive-word run treated as a leak (default {SHINGLE_WORDS})",
    )
    parser.add_argument(
        "--allow", action="append", default=[], metavar="TOKEN",
        help="token that may appear on both sides (repeatable); for genuine collisions only",
    )
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    args = parser.parse_args()

    if args.shingle < 3:
        sys.stderr.write("cannot run: --shingle must be at least 3\n")
        return 2

    vault_raw = read(args.archived_note, "archived note")

    # A log with nothing withheld has no redacted file, so the mandatory verification
    # step could not run at all and was in practice skipped. A skipped step is exactly
    # the escape a missed redaction needs, so the clean case gets its own assertion
    # rather than an exemption: the note must actively claim nothing was withheld.
    if args.no_withheld:
        if args.redacted_file is not None and Path(args.redacted_file).exists():
            sys.stderr.write(
                f"cannot run: --no-withheld passed but {args.redacted_file} exists. "
                "Either material was withheld or it was not.\n"
            )
            return 2
        fm, body = frontmatter_and_body(vault_raw)
        problems = []
        if re.search(r"^redacted:\s*true\b", fm, re.M | re.I):
            problems.append("frontmatter says `redacted: true` but no redacted file was given")
        if re.search(r"^redacted_count:\s*[1-9]", fm, re.M):
            problems.append("frontmatter declares a nonzero `redacted_count`")
        if VISIBLE_NOTICE.search(strip_hidden(body)):
            problems.append("the body carries a withheld notice")
        if not re.search(r"^redacted:\s*false\b", fm, re.M | re.I):
            problems.append("frontmatter must state `redacted: false` so the clean claim is explicit")
        if problems:
            sys.stderr.write(f"FAIL: clean-archive claim is inconsistent in {args.archived_note}\n")
            for problem in problems:
                sys.stderr.write(f"  - {problem}\n")
            sys.stderr.write("\nRoll back the archive. Do not prune the source log.\n")
            return 1
        if not args.quiet:
            print(f"OK  {args.archived_note}\n    verified clean: nothing withheld, and the note says so")
        return 0

    if args.redacted_file is None:
        sys.stderr.write(
            "cannot run: no redacted file given. Pass one, or pass --no-withheld to "
            "verify a log from which nothing was withheld.\n"
        )
        return 2
    withheld_raw = read(args.redacted_file, "redacted file")

    if not withheld_raw.strip():
        sys.stderr.write(
            f"cannot run: {args.redacted_file} is empty. A redacted file with no content "
            "means the withheld material was lost, which fails the archive.\n"
        )
        return 2

    vault_fm, vault_body = frontmatter_and_body(vault_raw)
    vault_norm = normalize(vault_raw)
    vault_tokens = words(vault_raw)
    vault_shingles = shingles(vault_tokens, args.shingle)

    failures: list[str] = []

    # 1. No withheld sentence survives anywhere in the archived note.
    for sentence in sentences(withheld_raw):
        if sentence in vault_norm:
            failures.append(f"withheld sentence present in archived note: {sentence[:90]!r}")

    # 2. No long word-run from the withheld text survives, which catches paraphrase that
    #    kept the load-bearing clause.
    for shingle in shingles(words(withheld_raw), args.shingle):
        if shingle in vault_shingles:
            failures.append(
                f"withheld {args.shingle}-word run present in archived note: "
                f"{' '.join(shingle)!r}"
            )

    # 3. Token sweep over the WHOLE archived note.
    #
    # Reviews closed several holes here by construction, and each one is a test now.
    # A withheld sentence under the length floor ("PersonC was fired.") survived both
    # the sentence check and the word-run check. An entity named only inside a
    # withheld block could be written into `people:` frontmatter, the leak this
    # capability's own documentation calls the subtlest it can produce. And a
    # credential-shaped value like `PIN: 1234` is neither long prose nor seven words,
    # so length was never the right axis for it.
    withheld_body = PROVENANCE.sub(" ", withheld_raw)
    allowed = {a.lower() for a in args.allow}

    def distinctive(text: str) -> set[str]:
        out = set()
        for t in IDENT_SPLIT.split(normalize(text)):
            if not t or t in STOPWORDS or t in allowed:
                continue
            if t.isdigit():
                if len(t) >= MIN_DIGIT_TOKEN:
                    out.add(t)
            elif len(t) >= 2:
                out.add(t)
        return out

    def entity_candidates(text: str) -> set[str]:
        """Tokens that look like a named subject rather than ordinary vocabulary.

        Used only for the filename and provenance checks. A review made a clean
        `2026-05-04-team-update.md` fail because withheld prose contained the word
        "team", while `amy-departure` passed because "amy" was under a length floor.
        Length was the wrong discriminator in both directions.

        Capitalisation is the better one for a slug check: a redaction subject is a
        proper noun in the withheld prose, and ordinary vocabulary is not. Tokens with
        no case system, which covers every non-Latin script, always qualify, because
        case cannot be used to rule them out.
        """
        out = set()
        for raw in re.findall(r"[^\W_][^\W_']*", PROVENANCE.sub(" ", text), re.UNICODE):
            low = raw.lower()
            if low in STOPWORDS or low in allowed:
                continue
            caseless = raw.lower() == raw.upper()
            if raw[:1].isupper() or caseless or CREDENTIAL_SHAPED.fullmatch(raw):
                if len(raw) >= 2 or caseless:
                    out.add(low)
        for m in CREDENTIAL_SHAPED.finditer(text):
            v = m.group(0).strip().lower()
            if v and v not in allowed:
                out.add(v)
        return out

    withheld_idents = distinctive(withheld_body)
    withheld_entities = entity_candidates(withheld_body)

    # Credential-shaped values are compared verbatim with no threshold of any kind.
    for m in CREDENTIAL_SHAPED.finditer(withheld_body):
        needle = normalize(m.group(0))
        if len(needle) >= 3 and needle in vault_norm and needle not in allowed:
            failures.append(
                f"credential-shaped value from the withheld text appears verbatim: {needle!r}"
            )

    # Structural frontmatter is excluded so `type: session-log` and a shared `date:`
    # do not read as leaks.
    content_fm = "\n".join(
        line for line in vault_fm.splitlines()
        if not any(line.lstrip('"\' ').startswith(k) and ":" in line for k in STRUCTURAL_KEYS)
    )
    searchable = f"{content_fm}\n{vault_body}"

    leaked_tokens = sorted(distinctive(searchable) & withheld_idents)
    if leaked_tokens:
        failures.append(
            "tokens from the withheld text appear in the archived note: "
            f"{leaked_tokens}. Remove them, or pass --allow for each that is genuinely "
            "unrelated to what was withheld."
        )

    # The filename and provenance are copies of the sanctum filename rather than
    # authored prose, so they compare against entity candidates and the fix differs:
    # a re-slug, not an edit.
    # Capitalisation identifies an entity in the withheld PROSE. A slug is lowercase by
    # construction, so the same test on that side rules out every real hit; the slug is
    # tokenised plainly and intersected with the entities found in the prose.
    slug_hits = sorted(distinctive(args.archived_note.stem) & withheld_entities)

    # A slug can name a subject without tokenising to it. `personc-leaving` splits to
    # "personc", which matches neither "person" nor "c", so a review carried a subject
    # through by concatenation. Compare the separator-stripped slug against each
    # withheld entity as a substring too. Four characters is the floor because shorter
    # entities collide with ordinary syllables.
    slug_joined = "".join(IDENT_SPLIT.split(normalize(args.archived_note.stem)))
    slug_hits += [
        e for e in sorted(withheld_entities)
        if len(e) >= 4 and e in slug_joined and e not in slug_hits
    ]
    if slug_hits:
        failures.append(
            f"archived note filename names a withheld subject: {slug_hits}. "
            "Re-slug from redacted content and set source_withheld: true."
        )

    # Provenance is read from parsed YAML rather than by line prefix. A review carried a
    # subject through `source: >-` and through a quoted `"source"` key, both valid YAML
    # that a startswith() check never sees.
    for value in provenance_values(vault_fm):
        hits = sorted(
            distinctive(value) & withheld_entities - {"sessions", "redacted"}
        )
        if hits:
            failures.append(
                f"provenance field names a withheld subject: {hits} in {value!r}. "
                "Omit source and set source_withheld: true."
            )

    # 4. A withheld block must leave a notice a reader can actually see. A review
    # satisfied the old substring search with an HTML comment, which renders as nothing.
    if not VISIBLE_NOTICE.search(strip_hidden(vault_body)):
        failures.append(
            "withheld material exists but the archived note carries no reader-visible "
            "'Withheld from archive' notice outside comments and code. A silent omission "
            "reads as a complete record."
        )

    if failures:
        sys.stderr.write(
            f"FAIL: {len(failures)} redaction leak(s) in {args.archived_note}\n"
        )
        for failure in failures:
            sys.stderr.write(f"  - {failure}\n")
        sys.stderr.write(
            "\nRoll back the archive. Do not prune the source log from sessions/.\n"
        )
        return 1

    if not args.quiet:
        print(
            f"OK  {args.archived_note}\n"
            f"    checked {len(sentences(withheld_raw))} withheld sentence(s) and "
            f"{len(shingles(words(withheld_raw), args.shingle))} {args.shingle}-word run(s)\n"
            f"    filename, source frontmatter and withheld notice all clean"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
