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

# Sentences shorter than this are dropped before comparison. Withheld blocks contain
# ordinary connective prose ("He said that.") that legitimately recurs in the surviving
# text, and matching on it would make every archive fail.
MIN_SENTENCE_CHARS = 24

# A run of this many consecutive words from a withheld block appearing in the archived note
# is treated as a leak even when no whole sentence matched. Catches a rewritten sentence
# that kept the incriminating clause.
SHINGLE_WORDS = 7

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n{2,}")
WORD = re.compile(r"[a-z0-9][a-z0-9']*")

# The redacted file carries its own provenance header naming the source log. That header is
# not withheld material, so comparing a slug against it makes every archive fail on its own
# filename. Strip it before building the token set the identifier checks use.
PROVENANCE = re.compile(r"^\s*#{1,6}\s*withheld from .*$", re.I | re.M)

# Slugs and paths are hyphen-joined identifiers. Tokenizing them as prose yields one long
# token that matches nothing, which is how a filename naming the redacted subject slipped
# through the first version of this check.
IDENT_SPLIT = re.compile(r"[^a-z0-9]+")


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
        description="Verify an archived archived note leaks nothing that was withheld from it."
    )
    parser.add_argument("archived_note", type=Path, help="the note written into vault/log/")
    parser.add_argument(
        "redacted_file",
        type=Path,
        help="the sessions/redacted/ file holding the withheld blocks",
    )
    parser.add_argument(
        "--shingle",
        type=int,
        default=SHINGLE_WORDS,
        help=f"consecutive-word run treated as a leak (default {SHINGLE_WORDS})",
    )
    parser.add_argument("--quiet", action="store_true", help="print only failures")
    args = parser.parse_args()

    if args.shingle < 3:
        sys.stderr.write("cannot run: --shingle must be at least 3\n")
        return 2

    vault_raw = read(args.archived_note, "archived note")
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

    # 3. The slug and the source frontmatter are copies of the sanctum filename and never
    #    pass through the body gate, so check them against the withheld text directly.
    #    Both sides split on non-alphanumerics: a hyphen-joined slug compared as prose is
    #    one opaque token, and matches nothing.
    withheld_body = PROVENANCE.sub(" ", withheld_raw)
    withheld_idents = {
        t for t in IDENT_SPLIT.split(normalize(withheld_body)) if len(t) > 3 and not t.isdigit()
    }

    def taint(text: str, ignore: set[str] = frozenset()) -> list[str]:
        found = {t for t in IDENT_SPLIT.split(normalize(text)) if t in withheld_idents}
        return sorted(found - ignore)

    slug_hits = taint(args.archived_note.stem)
    if slug_hits:
        failures.append(
            f"archived note filename shares distinctive tokens with withheld text: {slug_hits}. "
            "Re-slug from redacted content and set source_withheld: true."
        )

    for line in vault_fm.splitlines():
        if line.startswith(("source:", "source_path:")):
            src_hits = taint(line, ignore={"sessions", "redacted", "source", "path"})
            if src_hits:
                failures.append(
                    f"`{line.strip()}` shares distinctive tokens with withheld text: "
                    f"{src_hits}. Omit source and set source_withheld: true."
                )

    # 4. A withheld block must leave a visible notice. A silent hole reads as a complete
    #    record, which is the failure mode the trace rule exists to prevent.
    if "withheld from archive" not in vault_norm:
        failures.append(
            "withheld material exists but the archived note carries no 'Withheld from archive' "
            "notice. A silent omission reads as a complete record."
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
