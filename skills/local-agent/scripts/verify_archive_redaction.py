#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Assert that an archived note leaks nothing the redaction gate withheld.

The Archive capability (references/archive.md) decides what leaves the sanctum for the
archive, which is typically git-backed and may be replicated by a sync service. That
decision is made by a model reading a prose specification, and the same model then checks
its own work. This script makes the safety property executable. It re-reads the files
off disk and fails loudly when withheld text reached the archive, when the note carries a
confidentiality marker or a credential the gate should have caught on its own, or when
the note's frontmatter, placement and notices disagree with what was withheld.

Run it as step 7 of the archive sequence, before the source log is pruned:

    uv run scripts/verify_archive_redaction.py \\
        <archive>/log/2026/05/2026-05-04-topic.md \\
        <sanctum>/sessions/redacted/2026-05-04-topic.md \\
        --source <sanctum>/sessions/2026-05-04-topic.md

When nothing was withheld, omit the redacted file and pass --no-withheld; the note must
then say `redacted: false`, and the marker, credential, structure and source checks still
run. ``--source`` names the sanctum log that is about to be pruned. When it is given,
every sentence of the source must survive in the archived note or in the redacted file,
so a withheld block that never reached the redacted file is caught before the prune
destroys it.

Exit 0 means every check passed. Exit 1 means a leak or a broken invariant was found and
the archive must be rolled back. Exit 2 means the check could not run, which is also a
failure: the sequence fails closed, so an unrunnable check blocks the prune exactly like
a failed one. An unexpected exception exits 2 for the same reason.

The script never writes anything.
"""

from __future__ import annotations

import argparse
import re
import sys
import traceback
import unicodedata
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import _sanctum  # noqa: E402

# Sentences shorter than this are dropped from the sentence check. Withheld blocks contain
# ordinary connective prose ("He said that.") that legitimately recurs in the surviving text.
# Short sentences are NOT thereby unchecked: the token sweep below covers them, which is what
# closes the hole where "PersonC was fired." passed because it was 18 characters long.
MIN_SENTENCE_CHARS = 24

# Tokens this common carry no signal, so intersecting on them would flag every archive.
# Deliberately short: the cost of a false positive is a human reading one line, and the cost
# of a false negative is a secret in a synced repository that cannot be recalled.
# A word list reads better as prose than as two hundred quoted strings, hence the noqa.
STOPWORDS = frozenset(
    """
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
""".split()  # noqa: SIM905
)

# Frontmatter keys whose values are structural rather than content. Their words are not
# evidence of a leak.
STRUCTURAL_KEYS = frozenset(
    {"type", "date", "redacted", "redacted_count", "tags", "source_withheld"}
)

# Provenance keys, which are copies of the sanctum filename. They are checked against the
# withheld entities (see entity_candidates) and kept out of the prose token sweep.
PROVENANCE_KEYS = frozenset({"source", "source_path", "origin"})

# A digit run this long is an identifier, an amount, or a date-like figure. Exactly the
# shape of the thing worth withholding, so digits are checked rather than skipped.
MIN_DIGIT_TOKEN = 4

# A run of this many consecutive words from a withheld block appearing in the archived note
# is treated as a leak even when no whole sentence matched. Catches a rewritten sentence
# that kept the incriminating clause. The upper bound exists because a very long run
# matches nothing and silently switches the check off.
SHINGLE_WORDS = 7
MAX_SHINGLE_WORDS = 12

# How many characters of a leaked sentence a failure message reprints. Enough to find the
# line, short enough that the report is not a second copy of the block.
PREVIEW_CHARS = 60

# The closed vocabulary a withheld notice may name. references/archive.md, section 3.
CATEGORIES = frozenset(
    {"personnel", "compensation", "third-party-private", "security", "marked-confidential"}
)

# Marker tokens from references/archive.md, section 1. A token found in a line's prefix
# (its first bold span, or its first clause up to a colon) marks the line. The document
# calls the list illustrative; this is the executable floor beneath the model's judgment.
# Keep the two lists identical.
PREFIX_MARKERS = (
    "confidential", "never repeat", "do not repeat", "do not share", "not for sharing",
    "do not put in", "do not surface", "never surface", "off the record",
    "keep this between", "keep it confidential", "in confidence", "private", "sensitive",
    "internal only", "nda", "under embargo", "unannounced", "between us", "stays between",
    "don't tell", "do not tell", "keep this quiet", "not to be shared", "off books",
    "eyes only",
)  # fmt: skip

# Tokens that mark a block wherever they sit in a sentence. Bare "private" and
# "sensitive" are excluded because they occur in ordinary prose ("private repo",
# "sensitive data"), so they count only in prefix position.
ANYWHERE_MARKERS = (
    *(t for t in PREFIX_MARKERS if t not in {"private", "sensitive"}),
    "told me privately",
    "told us privately",
)

# Shapes the fail-closed rule withholds under `security` with no further analysis. The
# failure message names the shape and the line number only, never the value.
CREDENTIAL_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("slack token", re.compile(r"\bxox[abcdeprs]-[A-Za-z0-9-]{8,}")),
    ("slack app token", re.compile(r"\bxapp-[A-Za-z0-9-]{8,}")),
    ("github token", re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}")),
    ("github fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    ("openai or anthropic key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    (
        "oauth token or credentials file path",
        re.compile(r"[\w~./-]*(?:token|credential)s?\.json\b", re.IGNORECASE),
    ),
    ("cookie header", re.compile(r"\bcookie:\s*\S{16,}", re.IGNORECASE)),
)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
# Unicode-aware on purpose: an ASCII word pattern sees nothing in Cyrillic, Greek or CJK
# text, so a withheld sentence in any of those scripts would pass every comparison.
WORD = re.compile(r"[^\W_][^\W_']*", re.UNICODE)
DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

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
PROVENANCE = re.compile(r"^\s*#{1,6}\s*withheld from .*$", re.IGNORECASE | re.MULTILINE)

# Slugs and paths are hyphen-joined identifiers. Tokenizing them as prose yields one long
# token that matches nothing, which is how a filename naming the redacted subject slipped
# through the first version of this check.
IDENT_SPLIT = re.compile(r"[\W_]+", re.UNICODE)

# A notice a reader sees when the markdown is rendered.
VISIBLE_NOTICE = re.compile(r"withheld\s+from\s+archive", re.IGNORECASE)

# Constructs that render as nothing. A notice hidden in one of these is not a notice.
HIDDEN = re.compile(r"<!--.*?-->|```.*?```|~~~.*?~~~", re.DOTALL)

# The notice shape references/archive.md section 3 prescribes.
NOTICE_HEAD = re.compile(r"^\s*>\s*\[!warning\]\s*withheld from archive\s*$", re.IGNORECASE)
NOTICE_COUNT = re.compile(
    r"^(?P<n>\d+)\s+blocks?\s+withheld:\s*"
    r"(?P<cats>[a-z][a-z-]*(?:\s*,\s*[a-z][a-z-]*)*)\.?(?=\s|$)",
    re.IGNORECASE,
)

# Copies of a sanctum filename inside the body: a notice path, or a merge heading.
SESSIONS_PATH = re.compile(r"sessions/[\w./-]+", re.UNICODE)
ARCHIVED_FROM = re.compile(r"^#{1,6}\s*archived from\s+(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

# Frontmatter keys in any YAML spelling: bare or quoted.
FM_KEY = re.compile(r"""^\s*['"]?(?P<k>[A-Za-z0-9_-]+)['"]?\s*:(?:\s+(?P<v>.*))?$""")
FM_LIST_ITEM = re.compile(r"^\s+-\s+(.*)$")

LINE_LEAD = re.compile(r"^[\s>]*(?:(?:[-*+]|\d+[.)])\s+)?(?:#{1,6}\s+)?")
BOLD_SPAN = re.compile(r"^(?:\*\*|__)(.+?)(?:\*\*|__)")

# Characters that hide a leak from a byte comparison while leaving it readable: zero-width
# joiners and spaces, word joiner, soft hyphen, and the byte-order mark.
INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)

# Letters from other scripts that render like Latin ones. NFKC does not fold these, so a
# name retyped with one Cyrillic vowel would otherwise pass every comparison. The fold is
# applied to both sides, so genuine Cyrillic or Greek text still matches itself. Written as
# escapes so the mapping is visible in review.
CONFUSABLES = str.maketrans(
    {
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
        "\u0443": "y", "\u0445": "x", "\u0456": "i", "\u0458": "j", "\u0455": "s",
        "\u04bb": "h", "\u0501": "d", "\u051b": "q", "\u051d": "w",
        "\u0410": "a", "\u0412": "b", "\u0415": "e", "\u041a": "k", "\u041c": "m",
        "\u041d": "h", "\u041e": "o", "\u0420": "p", "\u0421": "c", "\u0422": "t",
        "\u0425": "x", "\u0406": "i", "\u0408": "j", "\u0405": "s",
        "\u03b1": "a", "\u03bf": "o", "\u03c1": "p", "\u03bd": "v", "\u03b9": "i",
        "\u0391": "a", "\u0392": "b", "\u0395": "e", "\u0396": "z", "\u0397": "h",
        "\u0399": "i", "\u039a": "k", "\u039c": "m", "\u039d": "n", "\u039f": "o",
        "\u03a1": "p", "\u03a4": "t", "\u03a5": "y", "\u03a7": "x",
        "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    }
)  # fmt: skip


def normalize(text: str) -> str:
    """Fold away the differences that hide a leak: case, unicode form, diacritics,
    look-alike letters, invisible characters, whitespace runs, and markdown emphasis.
    A secret retyped in bold is the same secret."""
    text = text.translate(INVISIBLE).translate(CONFUSABLES)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[*_`~\[\]()>#|]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def words(text: str) -> list[str]:
    return WORD.findall(normalize(text))


def shingles(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    if len(tokens) < n:
        return set()
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def split_label(text: str) -> tuple[str, str]:
    """Split a line or sentence into the span the marker rule inspects and the rest.
    The span is, after leading whitespace, quote markers, bullets and heading hashes,
    the first bold span or the first clause up to a colon. Both parts are empty strings
    when the text has neither."""
    stripped = LINE_LEAD.sub("", text, count=1)
    bold = BOLD_SPAN.match(stripped)
    if bold:
        return bold.group(1), stripped[bold.end() :]
    if ":" in stripped[:80]:
        label, _, rest = stripped.partition(":")
        return label, rest
    return "", ""


def sentences(text: str) -> list[str]:
    """Normalised sentences of at least MIN_SENTENCE_CHARS. A sentence that opens with a
    label ("**Confidential:** Priya resigned.") also yields the text after the label,
    because a model that strips the marker and keeps the rest leaves a fragment that is
    shorter than the shingle and differs from the whole sentence."""
    out = []
    for raw in SENTENCE_SPLIT.split(text):
        label, remainder = split_label(raw)
        for candidate in (raw, remainder if label else ""):
            cleaned = normalize(candidate)
            if len(cleaned) >= MIN_SENTENCE_CHARS:
                out.append(cleaned)
    return out


class WordStream:
    """A token sequence that answers whether a run of words occurs in it, independent of
    the punctuation, emphasis and line breaks around the words."""

    def __init__(self, text: str, shingle: int) -> None:
        self.tokens = words(text)
        self.joined = " " + " ".join(self.tokens) + " "
        self.shingles = shingles(self.tokens, shingle)

    def contains_run(self, tokens: list[str]) -> bool:
        return bool(tokens) and (" " + " ".join(tokens) + " ") in self.joined


def strip_hidden(text: str) -> str:
    """Body with HTML comments and fenced code removed.

    A review satisfied the visible-notice check with `<!-- Withheld from archive -->`,
    which renders as nothing at all. A notice that no reader sees is the silent hole the
    rule exists to prevent, so hidden constructs are removed before looking for it.
    """
    return HIDDEN.sub(" ", text)


def read(path: Path, label: str) -> str:
    """File text with a byte-order mark dropped and line endings folded to LF. Before
    this, a BOM made the frontmatter parse miss and the source check was skipped
    without a word."""
    if not path.is_file():
        sys.stderr.write(f"cannot run: {label} does not exist at {path}\n")
        raise SystemExit(2)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        sys.stderr.write(f"cannot run: {label} unreadable at {path}: {exc}\n")
        raise SystemExit(2) from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """Return the frontmatter lines and the body. None when the block is absent or never
    closed, which the caller treats as a failure: an unparsed frontmatter would skip the
    source and provenance checks silently."""
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() in {"---", "..."}:
            return lines[1:i], "\n".join(lines[i + 1 :])
    return None, text


def frontmatter_entries(lines: list[str]) -> list[tuple[str | None, list[str], list[str]]]:
    """Group frontmatter into (key, values, raw lines). Handles `key: value`,
    `key: [a, b]`, `key:` followed by `  - item` lines, quoted keys, and block scalars
    (`key: |` or `key: >-` with the value on the indented lines that follow).

    A line-prefix check misses `source: >-` with the value on following lines, and
    misses a `"source"` key. Both are valid YAML and a review carried a redaction
    subject through each. Every value is a list so the merge case, where `source` names
    two sanctum logs, needs no special path.
    """
    entries: list[tuple[str | None, list[str], list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = FM_KEY.match(line)
        if not match:
            item = FM_LIST_ITEM.match(line)
            if item and entries and entries[-1][0] is not None:
                entries[-1][1].append(item.group(1).strip().strip("\"'"))
                entries[-1][2].append(line)
            else:
                entries.append((None, [], [line]))
            i += 1
            continue
        key, value = match.group("k"), (match.group("v") or "").strip()
        raw = [line]
        values: list[str] = []
        i += 1
        if value.startswith(("|", ">")):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1].isspace()):
                block.append(lines[i].strip())
                raw.append(lines[i])
                i += 1
            values.append(" ".join(b for b in block if b))
        elif value.startswith("[") and value.endswith("]"):
            values.extend(
                v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()
            )
        elif value:
            values.append(value.strip("\"'"))
        entries.append((key, values, raw))
    return entries


def parse_frontmatter(lines: list[str]) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for key, values, _ in frontmatter_entries(lines):
        if key is not None:
            fields.setdefault(key.lower(), []).extend(values)
    return fields


def provenance_values(frontmatter: str) -> list[str]:
    """Every provenance value, including block scalars and quoted keys."""
    fields = parse_frontmatter(frontmatter.splitlines())
    return [v for k in sorted(PROVENANCE_KEYS) for v in fields.get(k, [])]


def notices(body: str) -> list[tuple[int, str]]:
    """Every `> [!warning] Withheld from archive` callout in the body, as (line number,
    text of the continuation lines with the quote markers removed)."""
    lines = body.split("\n")
    found: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        if NOTICE_HEAD.match(lines[i]):
            start = i
            i += 1
            parts: list[str] = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                parts.append(lines[i].lstrip()[1:].strip())
                i += 1
            found.append((start + 1, " ".join(parts)))
            continue
        i += 1
    return found


def strip_notices(body: str) -> str:
    """Body with every notice callout blanked, line count preserved so line numbers in
    failure messages still point at the right place."""
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        if NOTICE_HEAD.match(lines[i]):
            lines[i] = ""
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                lines[i] = ""
                i += 1
            continue
        i += 1
    return "\n".join(lines)


def marker_regex(tokens: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = [r"\s+".join(re.escape(w) for w in t.split()) for t in tokens]
    return re.compile(r"(?<![a-z0-9'])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")


PREFIX_MARKER_RE = marker_regex(PREFIX_MARKERS)
ANYWHERE_MARKER_RE = marker_regex(ANYWHERE_MARKERS)


def mask(text: str, generic: bool = True) -> str:
    """Replace every credential-shaped span with a placeholder. A failure report is read
    by a model and often pasted onward, so it must not become a second copy of a secret.
    `generic=False` masks only the named shapes, for filenames whose digits are a date."""
    for _, pattern in CREDENTIAL_SHAPES:
        text = pattern.sub("<credential>", text)
    return CREDENTIAL_SHAPED.sub("<value>", text) if generic else text


def show_token(token: str) -> str:
    """A token as it may appear in a report. Digit runs and credential-shaped values are
    described by length only, because the digits are the secret."""
    if token.isdigit() or CREDENTIAL_SHAPED.fullmatch(token) or mask(token) != token:
        return f"<{len(token)}-char value>"
    return token


def preview(text: str) -> str:
    return mask(" ".join(WORD.findall(normalize(text))))[:PREVIEW_CHARS]


@dataclass
class Archive:
    """Everything the checks need, parsed once."""

    note_path: Path
    note_raw: str
    fm_lines: list[str]
    body: str
    redacted_path: Path | None
    withheld_raw: str
    source_path: Path | None
    source_raw: str
    shingle: int
    allowed: frozenset[str]
    archive_root: Path | None
    failures: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.frontmatter = parse_frontmatter(self.fm_lines)

    def fail(self, message: str) -> None:
        self.failures.append(message)

    @property
    def date(self) -> str | None:
        values = self.frontmatter.get("date", [])
        match = DATE.match(values[0]) if values else None
        if not match:
            return None
        try:
            Date.fromisoformat(match.group(0))
        except ValueError:
            return None
        return match.group(0)

    @property
    def sources(self) -> list[str]:
        return self.frontmatter.get("source", []) + self.frontmatter.get("source_path", [])

    @property
    def source_withheld(self) -> bool:
        return [v.lower() for v in self.frontmatter.get("source_withheld", [])] == ["true"]

    @property
    def withheld_body(self) -> str:
        return PROVENANCE.sub(" ", self.withheld_raw)


# Each check appends to archive.failures and returns nothing.


def check_structure(a: Archive) -> None:
    """The invariants references/archive.md states for where a note lands and what its
    frontmatter must say about provenance."""
    date = a.date
    if date is None:
        a.fail("frontmatter carries no valid `date: YYYY-MM-DD`")
        return
    stem = a.note_path.stem
    if not stem.startswith(date):
        a.fail(f"archived note slug {stem!r} does not start with date {date}")

    year, month = date[:4], date[5:7]
    resolved = a.note_path.resolve()
    parents = [p.name for p in resolved.parents[:3]]
    if parents != [month, year, "log"]:
        a.fail(
            f"archived note is not under log/{year}/{month}/ "
            f"(found {'/'.join(reversed(parents))}/). The month directory comes from the "
            "date inside the filename."
        )
    elif a.archive_root is not None and resolved.parents[3] != a.archive_root:
        a.fail(
            f"archived note is not inside the configured archive at {a.archive_root}; "
            "it must sit at <archive>/log/YYYY/MM/"
        )

    sources, withheld_flag = a.sources, a.source_withheld
    if sources and withheld_flag:
        a.fail("frontmatter carries both `source` and `source_withheld: true`; omit `source`")
    if not sources and not withheld_flag:
        a.fail("frontmatter carries neither `source` nor `source_withheld: true`")
    names = {Path(s).name for s in sources}
    stems = []
    for value in sources:
        name = Path(value).name
        stems.append(Path(name).stem)
        if not value.startswith("sessions/") or not name.endswith(".md"):
            a.fail(f"`source: {value}` is not a `sessions/<name>.md` path")
        if not name.startswith(date):
            a.fail(f"`source: {value}` does not carry the note's date {date}")
    if sources and not any(
        stem == s or (DATE.fullmatch(s) and stem.startswith(s)) for s in stems
    ):
        a.fail(
            f"archived note slug {stem!r} matches none of its source filenames {stems}. "
            "The slug is the sanctum filename, or the date plus a derived topic for a bare "
            "day log; a re-slugged note sets `source_withheld: true` instead."
        )

    if a.redacted_path is not None:
        if not a.redacted_path.stem.startswith(date):
            a.fail(
                f"redacted file {a.redacted_path.name!r} does not carry the note's date {date}"
            )
        if sources and a.redacted_path.name not in names:
            a.fail(
                f"redacted file {a.redacted_path.name!r} is not named after any `source` "
                "filename; it must be sessions/redacted/<original-filename>.md"
            )
    if a.source_path is not None:
        if sources and a.source_path.name not in names:
            a.fail(
                f"--source {a.source_path.name!r} is not listed under `source` in the "
                "frontmatter"
            )
        if a.redacted_path is not None and a.redacted_path.name != a.source_path.name:
            a.fail(
                f"redacted file {a.redacted_path.name!r} is not named after --source "
                f"{a.source_path.name!r}"
            )


def check_clean_claim(a: Archive) -> None:
    """A log with nothing withheld has no redacted file, so the mandatory verification
    step could not run at all and was in practice skipped. A skipped step is exactly the
    escape a missed redaction needs, so the clean case gets its own assertion: the note
    must actively claim nothing was withheld."""
    fm = "\n".join(a.fm_lines)
    if re.search(r"^redacted:\s*true\b", fm, re.MULTILINE | re.IGNORECASE):
        a.fail("frontmatter says `redacted: true` but no redacted file was given")
    if re.search(r"^redacted_count:\s*[1-9]", fm, re.MULTILINE):
        a.fail("frontmatter declares a nonzero `redacted_count`")
    if VISIBLE_NOTICE.search(strip_hidden(a.body)):
        a.fail("the body carries a withheld notice")
    if not re.search(r"^redacted:\s*false\b", fm, re.MULTILINE | re.IGNORECASE):
        a.fail("frontmatter must state `redacted: false` so the clean claim is explicit")


def check_notices(a: Archive) -> None:
    """A withheld block must leave a notice a reader can actually see, in the shape and
    vocabulary section 3 of references/archive.md prescribes. A review satisfied an older
    substring search with an HTML comment, which renders as nothing."""
    if [v.lower() for v in a.frontmatter.get("redacted", [])] != ["true"]:
        a.fail("withheld material exists but the frontmatter lacks `redacted: true`")
    counts = a.frontmatter.get("redacted_count", [])
    declared = int(counts[0]) if counts and counts[0].isdigit() else None
    if declared is None or declared < 1:
        a.fail("withheld material exists but the frontmatter lacks a positive `redacted_count`")

    found = notices(strip_hidden(a.body))
    if not found:
        a.fail(
            "withheld material exists but the archived note carries no reader-visible "
            "'> [!warning] Withheld from archive' notice outside comments and code. A "
            "silent omission reads as a complete record."
        )
        return

    expected_path = f"sessions/redacted/{a.redacted_path.name}" if a.redacted_path else None
    total = 0
    for line_no, text in found:
        match = NOTICE_COUNT.match(text)
        if not match:
            a.fail(
                f"notice at body line {line_no} does not read `N block(s) withheld: "
                "<category>`; the category comes from the closed vocabulary and nothing else"
            )
            continue
        total += int(match.group("n"))
        cats = [c.strip().lower() for c in match.group("cats").split(",")]
        bad = [c for c in cats if c not in CATEGORIES]
        if bad:
            a.fail(
                f"notice at body line {line_no} names {bad}, which is outside the category "
                f"vocabulary {sorted(CATEGORIES)}"
            )
        rest = text[match.end() :]
        if a.source_withheld and re.search(r"sessions/|\.md\b", rest, re.IGNORECASE):
            a.fail(
                f"notice at body line {line_no} prints a path although "
                "`source_withheld: true`; print the category and the date only"
            )
        elif expected_path:
            for path in SESSIONS_PATH.findall(rest):
                if path.rstrip(".") != expected_path:
                    a.fail(
                        f"notice at body line {line_no} points at {path!r}; the withheld "
                        f"text is at {expected_path}"
                    )
    if declared is not None and total != declared:
        a.fail(
            f"notices declare {total} withheld block(s) in total; frontmatter says "
            f"redacted_count: {declared}"
        )


def check_withheld_text(a: Archive) -> None:
    allowed = a.allowed
    note = WordStream(a.note_raw, a.shingle)

    # 1. No withheld sentence survives anywhere in the archived note, whatever the
    #    punctuation, emphasis or line breaks around it, and with or without its label.
    seen: set[str] = set()
    for sentence in sentences(a.withheld_raw):
        tokens = WORD.findall(sentence)
        key = " ".join(tokens)
        if key not in seen and note.contains_run(tokens):
            seen.add(key)
            a.fail(f"withheld sentence present in archived note: {preview(sentence)!r}")

    # 2. No long word-run from the withheld text survives, which catches paraphrase that
    #    kept the load-bearing clause.
    for shingle in sorted(shingles(words(a.withheld_raw), a.shingle)):
        if shingle in note.shingles:
            a.fail(
                f"withheld {a.shingle}-word run present in archived note: "
                f"{mask(' '.join(shingle))!r}"
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
    withheld_body = a.withheld_body

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
        no case system, which covers every non-Latin script without case, always
        qualify, because case cannot be used to rule them out. Candidates are
        normalised the same way the slug is, so a diacritic or a Cyrillic letter in
        the prose still meets its plain spelling in the filename.
        """
        out = set()
        cased = unicodedata.normalize("NFC", PROVENANCE.sub(" ", text).translate(INVISIBLE))
        for raw in WORD.findall(cased):
            low = normalize(raw)
            if not low or low in STOPWORDS or low in allowed:
                continue
            caseless = raw.lower() == raw.upper()
            looks_named = raw[:1].isupper() or caseless or CREDENTIAL_SHAPED.fullmatch(raw)
            if looks_named and (len(low) >= 2 or caseless):
                out.add(low)
        for m in CREDENTIAL_SHAPED.finditer(text):
            v = normalize(m.group(0))
            if v and v not in allowed:
                out.add(v)
        return out

    # The note's own date is public by construction: it is in the filename and the path.
    # Without this, a withheld sentence mentioning any day of the same year fails every
    # archive on the year alone, and a gate that fails ordinary archives gets bypassed.
    own_date = set(IDENT_SPLIT.split(a.date or "")) - {""}
    withheld_idents = distinctive(withheld_body) - own_date
    withheld_entities = entity_candidates(withheld_body) - own_date

    # Credential-shaped values are compared verbatim with no threshold of any kind, and
    # reported by line number only.
    note_lines = a.note_raw.split("\n")
    note_norm = normalize(a.note_raw)
    reported: set[str] = set()
    for m in CREDENTIAL_SHAPED.finditer(withheld_body):
        # Trailing punctuation belongs to the sentence, and `PIN: 1234.` must still
        # match `PIN: 1234`.
        needle = normalize(m.group(0)).rstrip(".,;:!?'\"")
        if len(needle) < 3 or needle in allowed or needle in reported or needle in own_date:
            continue
        if needle in note_norm:
            reported.add(needle)
            where = next(
                (f"line {n}" for n, line in enumerate(note_lines, 1)
                 if needle in normalize(line)),
                "a span crossing lines",
            )
            a.fail(
                "credential-shaped value from the withheld text appears verbatim in the "
                f"archived note at {where} (value not printed)"
            )

    # Structural and provenance frontmatter is excluded so `type: session-log`, a shared
    # `date:` and the sanctum filename do not read as leaks. Provenance is checked below
    # against entity candidates, which is the right test for a filename.
    content_fm = "\n".join(
        line
        for key, _, raw in frontmatter_entries(a.fm_lines)
        if key is None or key.lower() not in STRUCTURAL_KEYS | PROVENANCE_KEYS
        for line in raw
    )
    body_prose = ARCHIVED_FROM.sub(" ", SESSIONS_PATH.sub(" ", a.body))
    searchable = f"{content_fm}\n{body_prose}"

    leaked_tokens = sorted(distinctive(searchable) & withheld_idents)
    if leaked_tokens:
        a.fail(
            "tokens from the withheld text appear in the archived note: "
            f"{[show_token(t) for t in leaked_tokens]}. Remove them, or pass --allow for "
            "each that is genuinely unrelated to what was withheld."
        )

    # The filename and provenance are copies of the sanctum filename rather than
    # authored prose, so they compare against entity candidates and the fix differs:
    # a re-slug, not an edit.
    # Capitalisation identifies an entity in the withheld PROSE. A slug is lowercase by
    # construction, so the same test on that side rules out every real hit; the slug is
    # tokenised plainly and intersected with the entities found in the prose.
    slug = a.note_path.stem
    slug_hits = sorted(distinctive(slug) & withheld_entities)

    # A slug can name a subject without tokenising to it. `personc-leaving` splits to
    # "personc", which matches neither "person" nor "c", so a review carried a subject
    # through by concatenation. Compare the separator-stripped slug against each
    # withheld entity as a substring too. Four characters is the floor because shorter
    # entities collide with ordinary syllables.
    slug_joined = "".join(IDENT_SPLIT.split(normalize(slug)))
    slug_hits += [
        e for e in sorted(withheld_entities)
        if len(e) >= 4 and e in slug_joined and e not in slug_hits
    ]
    if slug_hits:
        a.fail(
            f"archived note filename names a withheld subject: "
            f"{[show_token(t) for t in slug_hits]}. Re-slug from redacted content and "
            "set source_withheld: true."
        )

    # Provenance is read from parsed YAML rather than by line prefix. A review carried a
    # subject through `source: >-` and through a quoted `"source"` key, both valid YAML
    # that a startswith() check never sees. Notice paths and merge headings are the
    # same filename copied into the body.
    copies = [
        ("provenance field", v, "Omit source and set source_withheld: true.")
        for v in provenance_values("\n".join(a.fm_lines))
    ]
    copies += [
        ("path in the body", p, "Print the category and the date only.")
        for p in SESSIONS_PATH.findall(a.body)
    ]
    copies += [
        ("`Archived from` heading", h, "Re-slug and set source_withheld: true.")
        for h in ARCHIVED_FROM.findall(a.body)
    ]
    for label, value, fix in copies:
        hits = sorted(distinctive(value) & withheld_entities - {"sessions", "redacted"})
        if hits:
            a.fail(
                f"{label} names a withheld subject: {[show_token(t) for t in hits]} in "
                f"{mask(value, generic=False)!r}. {fix}"
            )


def check_markers(a: Archive) -> None:
    """The note itself must carry no confidentiality marker. A marked block is withheld
    whole, marker line included, so any marker that survives means the gate missed a
    block, whatever the redacted file says. Markers inside comments and code still
    count: they are in the file that syncs, rendered or not."""
    text = strip_notices(a.body)
    # Body line 1 is the line after the closing `---`, so report file line numbers.
    offset = len(a.fm_lines) + 3
    for line_no, line in enumerate(text.split("\n"), start=offset):
        label = normalize(split_label(line)[0])
        hit = PREFIX_MARKER_RE.search(label) if label else None
        if hit:
            a.fail(
                f"confidentiality marker {hit.group(0)!r} in the prefix of line {line_no}; "
                "the whole block must be withheld"
            )
    reported = set()
    for hit in ANYWHERE_MARKER_RE.finditer(normalize(text)):
        phrase = re.sub(r"\s+", " ", hit.group(0))
        if phrase not in reported:
            reported.add(phrase)
            a.fail(
                f"confidentiality marker {phrase!r} in the archived note body; the whole "
                "block must be withheld"
            )


def check_credentials(a: Archive) -> None:
    """Credential shapes anywhere in the note, frontmatter included. The failure names
    the shape and the line, never the value."""
    for line_no, line in enumerate(a.note_raw.split("\n"), start=1):
        for shape, pattern in CREDENTIAL_SHAPES:
            if pattern.search(line):
                a.fail(
                    f"credential-shaped value ({shape}) on line {line_no}; withhold under "
                    "`security`"
                )


def check_source(a: Archive) -> None:
    """Every sentence of the source log must survive in the archived note or in the
    redacted file. A sentence in neither was lost, and after the prune it is gone.
    Every redacted sentence must also occur in the source, or the withheld copy is not
    the verbatim record step 5 of the sequence promises."""
    note = WordStream(a.note_raw, a.shingle)
    withheld = WordStream(a.withheld_raw, a.shingle)
    source = WordStream(a.source_raw, a.shingle)

    def survives(text: str) -> bool:
        tokens = words(text)
        return not tokens or note.contains_run(tokens) or withheld.contains_run(tokens)

    lost = []
    for sentence in SENTENCE_SPLIT.split(a.source_raw):
        if survives(sentence):
            continue
        # A referring sentence withheld from a labelled paragraph leaves the label in the
        # note and the sentence in the redacted file; both halves must survive somewhere.
        label, remainder = split_label(sentence)
        if label and survives(label) and survives(remainder):
            continue
        lost.append(preview(sentence))
    for item in lost[:10]:
        a.fail(f"source sentence is in neither the archived note nor the redacted file: {item!r}")
    if len(lost) > 10:
        a.fail(f"...and {len(lost) - 10} more source sentence(s) missing from both")

    if a.redacted_path is None:
        return
    for sentence in SENTENCE_SPLIT.split(a.withheld_body):
        tokens = words(sentence)
        if tokens and not source.contains_run(tokens):
            a.fail(
                "redacted file sentence does not occur in the source log, so the withheld "
                f"copy is not verbatim: {preview(sentence)!r}"
            )


def run(a: Archive, no_withheld: bool) -> list[str]:
    check_structure(a)
    if no_withheld:
        check_clean_claim(a)
    else:
        check_notices(a)
        check_withheld_text(a)
    check_markers(a)
    check_credentials(a)
    if a.source_path is not None:
        check_source(a)
    return a.failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an archived note leaks nothing that was withheld from it."
    )
    parser.add_argument(
        "archived_note", type=Path, help="the note written into <archive>/log/YYYY/MM/"
    )
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
        "--source",
        type=Path,
        help="the sessions/ log about to be pruned; every sentence in it must survive in "
        "the archived note or the redacted file",
    )
    parser.add_argument(
        "--shingle",
        type=int,
        default=SHINGLE_WORDS,
        help=f"consecutive-word run treated as a leak (default {SHINGLE_WORDS}, "
        f"range 3 to {MAX_SHINGLE_WORDS})",
    )
    parser.add_argument(
        "--allow", action="append", default=[], metavar="TOKEN",
        help="token that may appear on both sides (repeatable); for genuine collisions only",
    )
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    return parser


def cannot_run(message: str) -> int:
    sys.stderr.write(f"cannot run: {message}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not 3 <= args.shingle <= MAX_SHINGLE_WORDS:
        return cannot_run(f"--shingle must be between 3 and {MAX_SHINGLE_WORDS}")

    note_raw = read(args.archived_note, "archived note")
    if not note_raw.strip():
        return cannot_run(f"archived note {args.archived_note} is empty")

    withheld_raw = ""
    if args.no_withheld:
        if args.redacted_file is not None and args.redacted_file.exists():
            return cannot_run(
                f"--no-withheld passed but {args.redacted_file} exists. "
                "Either material was withheld or it was not."
            )
        redacted_path = None
    else:
        if args.redacted_file is None:
            return cannot_run(
                "no redacted file given. Pass one, or pass --no-withheld to verify a log "
                "from which nothing was withheld."
            )
        redacted_path = args.redacted_file
        withheld_raw = read(redacted_path, "redacted file")
        if not withheld_raw.strip():
            return cannot_run(
                f"{redacted_path} is empty. A redacted file with no content means the "
                "withheld material was lost, which fails the archive."
            )
        parents = [p.name for p in redacted_path.resolve().parents[:2]]
        if parents != ["redacted", "sessions"]:
            return cannot_run(
                f"{redacted_path} is not under sessions/redacted/, which is the only place "
                "withheld material lives"
            )

    source_raw = ""
    if args.source is not None:
        source_raw = read(args.source, "source log")
        if not source_raw.strip():
            return cannot_run(f"source log {args.source} is empty")

    try:
        root = _sanctum.archive_root()
    except _sanctum.ArchiveMisconfigured as exc:
        return cannot_run(str(exc))

    fm_lines, body = split_frontmatter(note_raw)
    if fm_lines is None:
        sys.stderr.write(
            f"FAIL: {args.archived_note} has no closed frontmatter block, so date, source "
            "and redaction flags cannot be checked\n\nRoll back the archive. Do not prune "
            "the source log from sessions/.\n"
        )
        return 1

    archive = Archive(
        note_path=args.archived_note,
        note_raw=note_raw,
        fm_lines=fm_lines,
        body=body,
        redacted_path=redacted_path,
        withheld_raw=withheld_raw,
        source_path=args.source,
        source_raw=source_raw,
        shingle=args.shingle,
        allowed=frozenset(normalize(t) for t in args.allow),
        archive_root=root,
    )
    failures = run(archive, args.no_withheld)

    if failures:
        sys.stderr.write(f"FAIL: {len(failures)} redaction problem(s) in {args.archived_note}\n")
        for failure in failures:
            sys.stderr.write(f"  - {failure}\n")
        sys.stderr.write("\nRoll back the archive. Do not prune the source log from sessions/.\n")
        return 1

    if not args.quiet:
        if args.no_withheld:
            detail = "    verified clean: nothing withheld, and the note says so"
        else:
            detail = (
                f"    checked {len(sentences(withheld_raw))} withheld sentence(s) and "
                f"{len(shingles(words(withheld_raw), args.shingle))} {args.shingle}-word "
                "run(s)\n    filename, provenance, notices, markers and credentials all clean"
            )
        source_line = (
            f"\n    every sentence of {args.source.name} survives in the note or the "
            "redacted file"
            if args.source is not None
            else ""
        )
        print(f"OK  {args.archived_note}\n{detail}{source_line}")
    return 0


def entrypoint(argv: list[str] | None = None) -> int:
    """Convert any unexpected exception into exit 2. A traceback is still printed, and a
    crash must read as 'could not run' to the caller that decides whether to prune."""
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001, this is the fail-closed boundary of the whole gate
        traceback.print_exc()
        sys.stderr.write("cannot run: unexpected error, treat as a failed check\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
