#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
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
run. ``--source`` is required. It names the sanctum log that is about to be pruned, and
every sentence of it must survive in the archived note or in the redacted file, so a
withheld block that never reached the redacted file is caught before the prune destroys
it.

Exit 0 means every check passed. Exit 1 means a leak or a broken invariant was found and
the archive must be rolled back. Exit 2 means the check could not run, which is also a
failure: the sequence fails closed, so an unrunnable check blocks the prune exactly like
a failed one. An unexpected exception exits 2 for the same reason.

Failure messages locate a problem by line number and print no withheld text: a report
is read by a model and often pasted onward, so it must not become a second copy.

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
from functools import cached_property
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

# Frontmatter keys that hold structure, each with the exact shape its value takes. A value
# of that shape carries no words worth sweeping. Any other value is swept like prose: a
# review carried withheld names through `type:`, a `date:` suffix and `source_withheld:`,
# all of which this list once exempted whatever they held.
# `tags` is absent on purpose. Tags are free text and always swept.
STRUCTURAL_SHAPES = {
    "type": re.compile(r"session-log"),
    "date": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "redacted": re.compile(r"true|false", re.IGNORECASE),
    "redacted_count": re.compile(r"\d+"),
    "source_withheld": re.compile(r"true|false", re.IGNORECASE),
}

# Provenance keys, which are copies of the sanctum filename. They are checked against the
# withheld identifiers in their own pass, which names the right fix (a re-slug), and kept
# out of the prose token sweep.
PROVENANCE_KEYS = frozenset({"source", "source_path", "origin"})

# Words every sanctum path carries. They name the folder layout and are never evidence.
PATH_WORDS = frozenset({"sessions", "redacted", "log", "md"})

# A digit run this long is an identifier, an amount, or a date-like figure. Exactly the
# shape of the thing worth withholding, so digits are checked rather than skipped.
MIN_DIGIT_TOKEN = 4

# A run of this many consecutive words from a withheld block appearing in the archived note
# is treated as a leak even when no whole sentence matched. Catches a rewritten sentence
# that kept the incriminating clause. The lower bound exists because a run of two common
# words matches every archive; the upper bound exists because a very long run matches
# nothing and silently switches the check off.
SHINGLE_WORDS = 7
MIN_SHINGLE_WORDS = 3
MAX_SHINGLE_WORDS = 12

# What a failure message prints in place of a token taken from the withheld text.
WITHHELD_PLACEHOLDER = "<withheld>"

# How many lost source sentences are listed one by one before the rest are counted.
MAX_LOST_LISTED = 10

# The closed vocabulary a withheld notice may name. references/archive.md, section 3.
CATEGORIES = frozenset(
    {"personnel", "compensation", "third-party-private", "security", "marked-confidential"}
)

# Marker tokens from references/archive.md, section 1. A token found in a line's prefix
# (its first bold span, or its first clause up to a colon) marks the line. The document
# calls the list illustrative; this is the executable floor beneath the model's judgment.
# Keep the two lists identical; a test parses the document and compares them.
PREFIX_MARKERS = (
    "confidential", "never repeat", "do not repeat", "don't repeat", "do not share",
    "don't share", "not for sharing", "do not put in", "do not surface", "never surface",
    "off the record", "keep this between", "keep it confidential", "in confidence",
    "private", "sensitive", "secret", "internal only", "nda", "under embargo",
    "unannounced", "between us", "stays between", "don't tell", "do not tell",
    "keep this quiet", "not to be shared", "off books", "eyes only",
)  # fmt: skip

# Markers that count only in prefix position, because ordinary prose uses them ("private
# repo", "sensitive data", "rotated the client secret").
PREFIX_ONLY_MARKERS = frozenset({"private", "sensitive", "secret"})

# Inflections a marker token also matches. "Omar told Dana confidentially that" and "under
# confidentiality" mark a block exactly as "confidential" does.
MARKER_INFLECTIONS = {"confidential": "(?:ly|ity)?"}

# Tokens that mark a block wherever they sit in a sentence.
ANYWHERE_MARKERS = tuple(t for t in PREFIX_MARKERS if t not in PREFIX_ONLY_MARKERS)

# "told me privately", "told Dana privately", "said privately". The words between the
# verb and the adverb are usually a name, so a hit is reported by this label alone.
# Any script, so a name written in Cyrillic or CJK between the two words still counts.
PRIVATELY = re.compile(r"(?<![a-z0-9'])(?:told|said)(?:\s+[^\W\d_][\w']*){0,3}?\s+privately\b")
PRIVATELY_LABEL = "told or said ... privately"

# Hyphens and dashes read as spaces for marker matching, so "Off-the-record" and
# "internal\u2011only" meet "off the record" and "internal only".
# A quote mark that is not an apostrophe inside a word.
QUOTE_MARK = re.compile(r"""(?<![^\W_])'|'(?![^\W_])|["]""")

DASHES = re.compile("[-\u2010-\u2015\u2212\ufe58\ufe63\uff0d]")

# Labelled secret assignments, in prose, env, YAML and JSON spelling. Any value of four or
# more characters counts, a multiword passphrase included, with two narrow exceptions.
# A value that is only syntax holds no secret: a YAML typed null (`api_key: null`) or a
# bare environment variable reference (`password = "${DATABASE_PASSWORD}"`). A quoted
# "null" is a string and still counts. After `:` a value opening with a status word is a
# note about the credential: "Password: reset by IT on Friday".
_SECRET_LABEL = (
    r"(?:\b|(?<=_))"
    r"(?:password|passwd|pwd|passcode|secret(?:[_-]?key)?|api[_-]?key|client[_-]?secret)\b"
    r"""[*_"'`]*\s*"""
)
_SYNTAX_ONLY = r"""(?:(?:null|~)|["'`]?\$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*)["'`]?)[,;]?\s*$"""
CREDENTIAL_STATUS_WORDS = (
    "reset", "rotated", "changed", "updated", "expired", "expires", "revoked", "removed",
    "stored", "saved", "kept", "managed", "shared", "required", "needed", "missing",
    "unknown", "pending", "tbd", "none", "n/a", "see", "in", "via", "from", "at", "on",
    "ask", "check", "use", "is", "was", "are", "were", "will", "has", "had", "needs",
    "must", "should", "not", "no",
)  # fmt: skip
_STATUS = r"(?:" + "|".join(re.escape(w) for w in CREDENTIAL_STATUS_WORDS) + r")\b"
_SECRET_VALUE = (
    rf"""(?:=\s*(?!{_SYNTAX_ONLY})["'`]?[^\s"'`]{{4,}}"""
    rf"""|:[*_\s]*(?!{_SYNTAX_ONLY})(?!["'`]?{_STATUS})["'`]?[^\s"'`]{{4,}})"""
)

# A YAML block scalar indicator at the end of a line: the value sits on the indented lines
# that follow, so a per-line scan sees the label and the secret apart.
BLOCK_SCALAR = re.compile(r":[ \t]*[|>][-+]?\d*[ \t]*$")

# Shapes the fail-closed rule withholds under `security` with no further analysis. The
# failure message names the shape and the line number only, never the value.
CREDENTIAL_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("slack token", re.compile(r"\bxox[abcdeprs]-[A-Za-z0-9-]{8,}")),
    ("slack app token", re.compile(r"\bxapp-[A-Za-z0-9-]{8,}")),
    (
        "slack webhook",
        re.compile(r"\bhooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{16,}"),
    ),
    ("github token", re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}")),
    ("github fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("gitlab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}")),
    ("openai or anthropic key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}")),
    ("stripe key", re.compile(r"\b[rsp]k_(?:live|test)_[A-Za-z0-9]{10,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("pypi token", re.compile(r"\bpypi-[A-Za-z0-9_-]{32,}")),
    ("aws access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "aws secret access key",
        re.compile(
            r"\baws_?secret_?(?:access_?)?key\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{20,}",
            re.IGNORECASE,
        ),
    ),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    (
        "basic auth header",
        re.compile(
            r"""\bauthorization["']?\s*:\s*["']?basic\s+[A-Za-z0-9+/]{8,}=*""", re.IGNORECASE
        ),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    (
        "url with embedded password",
        re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
    ),
    (
        "password or secret assignment",
        re.compile(_SECRET_LABEL + _SECRET_VALUE, re.IGNORECASE | re.MULTILINE),
    ),
    (
        "oauth token or credentials file path",
        re.compile(r"[\w~./-]*(?:token|credential)s?\.json\b", re.IGNORECASE),
    ),
    ("cookie header", re.compile(r"""\bcookie["']?\s*:\s*["']?\S{16,}""", re.IGNORECASE)),
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
# filename. Strip it before building the token set the identifier checks use. The pattern
# stays on one line so line numbers in the stripped text still match the file.
PROVENANCE = re.compile(r"^[ \t]*#{1,6}[ \t]*withheld from .*$", re.IGNORECASE | re.MULTILINE)

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
HEADING = re.compile(r"^[ \t>]*#{1,6}[ \t]+(.+?)[ \t#]*$")

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
        "\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'", "\u201c": '"', "\u201d": '"',
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
    the first bold span or the first clause up to a colon. A heading with neither is its
    own label, so `## Private` marks the section beneath it. Both parts are empty strings
    when the text has none of these."""
    stripped = LINE_LEAD.sub("", text, count=1)
    bold = BOLD_SPAN.match(stripped)
    if bold:
        return bold.group(1), stripped[bold.end() :]
    if ":" in stripped[:80]:
        label, _, rest = stripped.partition(":")
        return label, rest
    first, _, rest = text.partition("\n")
    heading = HEADING.match(first)
    if heading:
        return heading.group(1), rest
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


def sentence_spans(text: str) -> list[tuple[int, str]]:
    """Every raw sentence of the text with the file line it starts on, so a failure can
    point at the sentence without reprinting it."""
    out = []
    pos = 0
    for match in [*SENTENCE_SPLIT.finditer(text), None]:
        end = match.start() if match else len(text)
        chunk = text[pos:end]
        lead = len(chunk) - len(chunk.lstrip())
        out.append((text.count("\n", 0, pos + lead) + 1, chunk))
        pos = match.end() if match else end
    return out


class WordStream:
    """A token sequence that answers whether a run of words occurs in it, and on which
    line, independent of the punctuation, emphasis and line breaks around the words.
    Tokenising line by line yields the same sequence as the whole text, because no token
    crosses a newline."""

    def __init__(self, text: str, shingle: int) -> None:
        self.tokens: list[str] = []
        self.lines: list[int] = []
        for line_no, line in enumerate(text.split("\n"), start=1):
            found = words(line)
            self.tokens += found
            self.lines += [line_no] * len(found)
        self.joined = " " + " ".join(self.tokens) + " "
        self.shingles = shingles(self.tokens, shingle)

    def find_run(self, tokens: list[str] | tuple[str, ...]) -> int | None:
        """The line the run starts on, or None when the run does not occur."""
        if not tokens:
            return None
        at = self.joined.find(" " + " ".join(tokens) + " ")
        if at < 0:
            return None
        # Token i starts after the (i+1)th space, so the spaces before the match count it.
        return self.lines[self.joined.count(" ", 0, at)]

    def contains_run(self, tokens: list[str]) -> bool:
        return self.find_run(tokens) is not None


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
    """Body with each notice's own wording blanked: the heading and the `N blocks withheld:
    <categories>` prefix. Everything after them in the callout stays, so a marker placed
    inside a notice is still caught. Line count is preserved so line numbers in failure
    messages still point at the right place."""
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        if NOTICE_HEAD.match(lines[i]):
            lines[i] = ""
            i += 1
            if i < len(lines) and lines[i].lstrip().startswith(">"):
                content = lines[i].lstrip()[1:].lstrip()
                count = NOTICE_COUNT.match(content)
                if count:
                    lines[i] = "> " + content[count.end() :].lstrip()
            continue
        i += 1
    return "\n".join(lines)


def marker_regex(tokens: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = [
        r"\s+".join(re.escape(w) for w in t.split()) + MARKER_INFLECTIONS.get(t, "")
        for t in tokens
    ]
    return re.compile(r"(?<![a-z0-9'])(?:" + "|".join(alternatives) + r")(?![a-z0-9])")


PREFIX_MARKER_RE = marker_regex(PREFIX_MARKERS)
ANYWHERE_MARKER_RE = marker_regex(ANYWHERE_MARKERS)


def marker_text(text: str) -> str:
    """Text as the marker rule reads it: normalised, with hyphens, dashes and quote marks
    as spaces. An apostrophe inside a word stays, so "don't" still reads as one word, and
    a quoted YAML key such as `'confidential':` reads as the bare word."""
    text = QUOTE_MARK.sub(" ", DASHES.sub(" ", normalize(text)))
    return re.sub(r"\s+", " ", text).strip()


# An underscore inside a snake_case identifier. `client_secret:` and `private_key:` are
# configuration keys, so a prefix-only marker word inside one marks nothing.
SNAKE_JOIN = re.compile(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])")


def prefix_marker(label: str) -> str | None:
    """The marker a prefix label carries, or None. A prefix-only word counts when it
    stands as a word of its own, outside any snake_case identifier."""
    if not label:
        return None
    joined = {m.group(0) for m in PREFIX_MARKER_RE.finditer(marker_text(SNAKE_JOIN.sub("", label)))}
    for hit in PREFIX_MARKER_RE.finditer(marker_text(label)):
        phrase = re.sub(r"\s+", " ", hit.group(0))
        if phrase not in PREFIX_ONLY_MARKERS or phrase in joined:
            return phrase
    return None


def mask(text: str, generic: bool = True) -> str:
    """Replace every credential-shaped span with a placeholder. A failure report is read
    by a model and often pasted onward, so it must not become a second copy of a secret.
    `generic=False` masks only the named shapes, for filenames whose digits are a date."""
    for _, pattern in CREDENTIAL_SHAPES:
        text = pattern.sub("<credential>", text)
    return CREDENTIAL_SHAPED.sub("<value>", text) if generic else text


def distinctive(text: str, allowed: frozenset[str]) -> set[str]:
    """Identifier tokens of a text: everything but stopwords, allowed tokens, single
    letters and digit runs shorter than MIN_DIGIT_TOKEN. Slugs and paths split on their
    separators, so a hyphen-joined filename yields its words."""
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


def entity_candidates(text: str, allowed: frozenset[str]) -> set[str]:
    """Tokens that look like a named subject: proper nouns, caseless-script words and
    credential-shaped values.

    Used for the concatenated-slug check, where a substring match on ordinary words would
    flag every archive. Capitalisation identifies a redaction subject in the withheld
    prose: it is a proper noun there, and ordinary vocabulary is not. Tokens with no case
    system, which covers every non-Latin script without case, always qualify, because
    case cannot be used to rule them out. Candidates are normalised the same way the slug
    is, so a diacritic or a Cyrillic letter in the prose still meets its plain spelling in
    the filename.
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


@dataclass
class Archive:
    """Everything the checks need, parsed once."""

    note_path: Path
    note_raw: str
    fm_lines: list[str]
    body: str
    redacted_path: Path | None
    withheld_raw: str
    source_path: Path
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

    @cached_property
    def own_date(self) -> set[str]:
        """The note's own date is public by construction: it is in the filename and the
        path. Without this, a withheld sentence mentioning any day of the same year fails
        every archive on the year alone, and a gate that fails ordinary archives gets
        bypassed."""
        return set(IDENT_SPLIT.split(self.date or "")) - {""}

    @cached_property
    def withheld_idents(self) -> set[str]:
        return distinctive(self.withheld_body, self.allowed) - self.own_date

    @cached_property
    def withheld_entities(self) -> set[str]:
        return entity_candidates(self.withheld_body, self.allowed) - self.own_date

    def hide(self, text: str) -> str:
        """Text as a failure message may print it: credential shapes masked, and every
        token from the withheld text replaced by a placeholder. A report that names the
        person a block was withheld about is a second copy of the secret."""
        hidden = self.withheld_idents | self.withheld_entities

        def replace(match: re.Match[str]) -> str:
            token = normalize(match.group(0))
            return WITHHELD_PLACEHOLDER if token in hidden - PATH_WORDS else match.group(0)

        return re.sub(r"[^\W_]+", replace, mask(text, generic=False).translate(INVISIBLE))


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
        a.fail(f"archived note slug {a.hide(stem)!r} does not start with date {date}")

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
            a.fail(f"`source: {a.hide(value)}` is not a `sessions/<name>.md` path")
        if not name.startswith(date):
            a.fail(f"`source: {a.hide(value)}` does not carry the note's date {date}")
    if sources and not any(
        stem == s or (DATE.fullmatch(s) and stem.startswith(s)) for s in stems
    ):
        a.fail(
            f"archived note slug {a.hide(stem)!r} matches none of its source filenames "
            f"{[a.hide(s) for s in stems]}. "
            "The slug is the sanctum filename, or the date plus a derived topic for a bare "
            "day log; a re-slugged note sets `source_withheld: true` instead."
        )

    source_name = a.hide(a.source_path.name)
    if a.redacted_path is not None:
        redacted_name = a.hide(a.redacted_path.name)
        if not a.redacted_path.stem.startswith(date):
            a.fail(f"redacted file {redacted_name!r} does not carry the note's date {date}")
        if sources and a.redacted_path.name not in names:
            a.fail(
                f"redacted file {redacted_name!r} is not named after any `source` "
                "filename; it must be sessions/redacted/<original-filename>.md"
            )
        if a.redacted_path.name != a.source_path.name:
            a.fail(f"redacted file {redacted_name!r} is not named after --source {source_name!r}")
    if sources and a.source_path.name not in names:
        a.fail(f"--source {source_name!r} is not listed under `source` in the frontmatter")


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
                f"notice at body line {line_no} names {[a.hide(c) for c in bad]}, which is "
                "outside the category "
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
                        f"notice at body line {line_no} points at {a.hide(path)!r}; "
                        f"the withheld text is at {a.hide(expected_path)}"
                    )
    if declared is not None and total != declared:
        a.fail(
            f"notices declare {total} withheld block(s) in total; frontmatter says "
            f"redacted_count: {declared}"
        )


def searchable_lines(a: Archive) -> list[tuple[int, str]]:
    """The note as (file line, text) pairs for the prose token sweep.

    Frontmatter is included except provenance keys and structural keys whose value has
    exactly its expected shape, so `type: session-log`, a shared `date:` and the sanctum
    filename do not read as leaks. Provenance and the filename copies in the body are
    checked separately, because the fix for them is a re-slug. Line numbers are file
    lines: line 1 is the opening `---`.
    """
    out: list[tuple[int, str]] = []
    line_no = 2
    for key, values, raw in frontmatter_entries(a.fm_lines):
        lowered = (key or "").lower()
        shape = STRUCTURAL_SHAPES.get(lowered)
        exempt = lowered in PROVENANCE_KEYS or (
            shape is not None and len(values) == 1 and shape.fullmatch(values[0]) is not None
        )
        if not exempt:
            out += [(line_no + i, line) for i, line in enumerate(raw)]
        line_no += len(raw)
    body_start = len(a.fm_lines) + 3
    for i, line in enumerate(a.body.split("\n")):
        out.append((body_start + i, ARCHIVED_FROM.sub(" ", SESSIONS_PATH.sub(" ", line))))
    return out


def check_withheld_text(a: Archive) -> None:
    note = WordStream(a.note_raw, a.shingle)

    # 1. No withheld sentence survives anywhere in the archived note, whatever the
    #    punctuation, emphasis or line breaks around it, and with or without its label.
    seen: set[str] = set()
    for sentence in sentences(a.withheld_body):
        tokens = WORD.findall(sentence)
        key = " ".join(tokens)
        line = None if key in seen else note.find_run(tokens)
        if line is not None:
            seen.add(key)
            a.fail(f"withheld sentence present in archived note at line {line} (text not printed)")

    # 2. No long word-run from the withheld text survives, which catches paraphrase that
    #    kept the load-bearing clause. One report per note line, because a leaked sentence
    #    carries many overlapping runs.
    run_lines = {
        note.find_run(shingle)
        for shingle in shingles(words(a.withheld_body), a.shingle)
        if shingle in note.shingles
    }
    for line in sorted(n for n in run_lines if n is not None):
        a.fail(
            f"withheld {a.shingle}-word run present in archived note at line {line} "
            "(text not printed)"
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
    allowed, own_date = a.allowed, a.own_date

    # Credential-shaped values are compared verbatim with no threshold of any kind, and
    # reported by line number only.
    note_lines = a.note_raw.split("\n")
    note_norm = normalize(a.note_raw)
    reported: set[str] = set()
    for m in CREDENTIAL_SHAPED.finditer(a.withheld_body):
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

    # Each leaked token is reported once, at the first line carrying it, by length only.
    by_line: dict[int, list[str]] = {}
    seen_tokens: set[str] = set()
    for line_no, line in searchable_lines(a):
        for token in sorted(distinctive(line, allowed) & a.withheld_idents - seen_tokens):
            seen_tokens.add(token)
            by_line.setdefault(line_no, []).append(f"<{len(token)}-char token>")
    if by_line:
        where = "; ".join(f"line {n}: {', '.join(found)}" for n, found in by_line.items())
        a.fail(
            f"tokens from the withheld text appear in the archived note at {where}; the "
            "tokens are not printed. Remove them, or pass --allow for each that is "
            "genuinely unrelated to what was withheld."
        )

    # The filename and provenance are copies of the sanctum filename rather than
    # authored prose, so they are checked on their own and the fix differs: a re-slug,
    # not an edit. A slug is lowercase by construction, so it is intersected with every
    # withheld identifier. A review carried "lease" and "layoffs" through a slug, a
    # `source:` value and a notice path when this compared against capitalised entities
    # only. Stopwords keep ordinary words such as "team" from failing a clean slug.
    slug = a.note_path.stem
    slug_hits = distinctive(slug, allowed) & a.withheld_idents

    # A slug can name a subject without tokenising to it. `personc-leaving` splits to
    # "personc", which matches neither "person" nor "c", so a review carried a subject
    # through by concatenation. Compare the separator-stripped slug against each
    # withheld entity as a substring too. Entities only, because a substring test on
    # ordinary words matches syllables everywhere; four characters is the floor because
    # shorter entities collide with ordinary syllables.
    slug_joined = "".join(IDENT_SPLIT.split(normalize(slug)))
    slug_hits |= {e for e in a.withheld_entities if len(e) >= 4 and e in slug_joined}
    if slug_hits:
        a.fail(
            f"archived note filename names a withheld subject ({len(slug_hits)} token(s), "
            "not printed). Re-slug from redacted content and set source_withheld: true."
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
        if distinctive(value, allowed) & a.withheld_idents - PATH_WORDS:
            a.fail(f"{label} names a withheld subject in {a.hide(value)!r}. {fix}")


def check_markers(a: Archive) -> None:
    """The note itself must carry no confidentiality marker, in the body or in a
    frontmatter value. A marked block is withheld whole, marker line included, so any
    marker that survives means the gate missed a block, whatever the redacted file says.
    Markers inside comments and code still count: they are in the file that syncs,
    rendered or not. A review put "Confidential, do not share: ..." in a `summary:` value
    while this read the body only, and then `confidential: <the secret>` while this read
    values only. A frontmatter key is the prefix label of its value, in any YAML spelling:
    quoted, nested, or holding a block scalar."""
    # (file line, text) pairs. Line 1 is the opening `---`.
    fm_values: list[tuple[int, str]] = []
    line_no = 2
    for key, values, raw in frontmatter_entries(a.fm_lines):
        fm_values += [(line_no, v) for v in values] if key is not None else [(line_no, raw[0])]
        line_no += len(raw)
    fm_raw = [(i + 2, line) for i, line in enumerate(a.fm_lines)]
    body_start = len(a.fm_lines) + 3
    body_lines = strip_notices(a.body).split("\n")
    body = [(body_start + i, line) for i, line in enumerate(body_lines)]

    prefix_reported: set[tuple[int, str]] = set()
    for line_no, text in fm_raw + fm_values + body:
        hit = prefix_marker(split_label(text)[0])
        if hit and (line_no, hit) not in prefix_reported:
            prefix_reported.add((line_no, hit))
            a.fail(
                f"confidentiality marker {hit!r} in the prefix of line {line_no}; "
                "the whole block must be withheld"
            )

    # The anywhere sweep reads the frontmatter values and the body each as running text,
    # so a phrase split across lines still matches.
    reported = set()
    for where, part in (
        ("frontmatter", " ".join(text for _, text in fm_values)),
        ("body", "\n".join(body_lines)),
    ):
        folded = marker_text(part)
        phrases = [re.sub(r"\s+", " ", hit.group(0)) for hit in ANYWHERE_MARKER_RE.finditer(folded)]
        if PRIVATELY.search(folded):
            phrases.append(PRIVATELY_LABEL)
        for phrase in phrases:
            if (where, phrase) not in reported:
                reported.add((where, phrase))
                a.fail(
                    f"confidentiality marker {phrase!r} in the archived note {where}; the "
                    "whole block must be withheld"
                )


def check_credentials(a: Archive) -> None:
    """Credential shapes anywhere in the note, frontmatter included. The failure names
    the shape and the line, never the value. A YAML block scalar (`password: >-` with the
    value indented below) is read as one line, label and value together."""
    lines = a.note_raw.split("\n")
    for i, line in enumerate(lines):
        candidate = line
        if BLOCK_SCALAR.search(line):
            indent = len(line) - len(line.lstrip())
            value = []
            for follow in lines[i + 1 :]:
                if follow.strip() and len(follow) - len(follow.lstrip()) <= indent:
                    break
                value.append(follow.strip())
            candidate = BLOCK_SCALAR.sub(": ", line) + " ".join(v for v in value if v)
        line_no = i + 1
        for shape, pattern in CREDENTIAL_SHAPES:
            if pattern.search(candidate):
                a.fail(
                    f"credential-shaped value ({shape}) on line {line_no}; withhold under "
                    "`security`"
                )


def check_source(a: Archive) -> None:
    """Every sentence of the source log must survive in the archived note or in the
    redacted file. A sentence in neither was lost, and after the prune it is gone.
    Every redacted sentence must also occur in the source, or the withheld copy is not
    the verbatim record step 5 of the sequence promises. A lost sentence is presumptively
    material that should have been withheld, so it is reported by line only."""
    note = WordStream(a.note_raw, a.shingle)
    withheld = WordStream(a.withheld_raw, a.shingle)
    source = WordStream(a.source_raw, a.shingle)

    def survives(text: str) -> bool:
        tokens = words(text)
        return not tokens or note.contains_run(tokens) or withheld.contains_run(tokens)

    lost = []
    for line_no, sentence in sentence_spans(a.source_raw):
        if survives(sentence):
            continue
        # A referring sentence withheld from a labelled paragraph leaves the label in the
        # note and the sentence in the redacted file; both halves must survive somewhere.
        label, remainder = split_label(sentence)
        if label and survives(label) and survives(remainder):
            continue
        lost.append(line_no)
    for line_no in lost[:MAX_LOST_LISTED]:
        a.fail(
            f"source sentence at line {line_no} of --source is in neither the archived note "
            "nor the redacted file (text not printed)"
        )
    if len(lost) > MAX_LOST_LISTED:
        a.fail(
            f"...and {len(lost) - MAX_LOST_LISTED} more source sentence(s) missing from both"
        )

    if a.redacted_path is None:
        return
    for line_no, sentence in sentence_spans(a.withheld_body):
        tokens = words(sentence)
        if tokens and not source.contains_run(tokens):
            a.fail(
                f"redacted file sentence at line {line_no} does not occur in the source log, "
                "so the withheld copy is not verbatim (text not printed)"
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
        help="required: the sessions/ log about to be pruned; every sentence in it must "
        "survive in the archived note or the redacted file",
    )
    parser.add_argument(
        "--shingle",
        type=int,
        default=SHINGLE_WORDS,
        help=f"consecutive-word run treated as a leak (default {SHINGLE_WORDS}, "
        f"range {MIN_SHINGLE_WORDS} to {MAX_SHINGLE_WORDS})",
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

    if not MIN_SHINGLE_WORDS <= args.shingle <= MAX_SHINGLE_WORDS:
        return cannot_run(
            f"--shingle must be between {MIN_SHINGLE_WORDS} and {MAX_SHINGLE_WORDS}"
        )

    # Without the source log nothing can tell a withheld block that reached neither file
    # from one that was never there, and the prune that follows destroys it. A review
    # passed a note that silently dropped a marked block as "verified clean".
    if args.source is None:
        return cannot_run(
            "--source is required. Name the sessions/ log about to be pruned, so a block "
            "that reached neither the archived note nor the redacted file is caught "
            "before the prune destroys it."
        )

    note_raw = read(args.archived_note, "archived note")
    if not note_raw.strip():
        return cannot_run(f"archived note {args.archived_note} is empty")

    withheld_raw = ""
    if args.no_withheld:
        if args.redacted_file is not None:
            return cannot_run(
                f"--no-withheld passed together with the redacted file {args.redacted_file}. "
                "Either material was withheld or it was not; pass one of the two."
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

    source_raw = read(args.source, "source log")
    if not source_raw.strip():
        return cannot_run(f"source log {args.source} is empty")

    try:
        root = _sanctum.archive_root()
    except _sanctum.ArchiveMisconfigured as exc:
        return cannot_run(str(exc))

    fm_lines, body = split_frontmatter(note_raw)
    archive = Archive(
        note_path=args.archived_note,
        note_raw=note_raw,
        fm_lines=fm_lines or [],
        body=body,
        redacted_path=redacted_path,
        withheld_raw=withheld_raw,
        source_path=args.source,
        source_raw=source_raw,
        shingle=args.shingle,
        allowed=frozenset(normalize(t) for t in args.allow),
        archive_root=root,
    )
    # The note's path carries its slug, so it is printed through the same mask as every
    # failure message.
    shown = archive.hide(str(args.archived_note))
    if fm_lines is None:
        sys.stderr.write(
            f"FAIL: {shown} has no closed frontmatter block, so date, source and redaction "
            "flags cannot be checked\n\nRoll back the archive. Do not prune the source log "
            "from sessions/.\n"
        )
        return 1

    failures = run(archive, args.no_withheld)

    if failures:
        sys.stderr.write(f"FAIL: {len(failures)} redaction problem(s) in {shown}\n")
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
