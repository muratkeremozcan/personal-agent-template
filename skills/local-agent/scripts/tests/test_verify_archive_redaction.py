#!/usr/bin/env python3
"""Tests for verify_archive_redaction.py, the executable half of the archive redaction gate.

Every test builds a small tree shaped like the real one (<archive>/log/YYYY/MM/ beside a
sanctum holding sessions/ and sessions/redacted/) from the synthetic fixtures under
fixtures/redaction/, mutates one thing, and asserts the exit code and the message that
names the hole. Exit 0 is the only outcome that permits a prune. No test touches a real
sanctum or a real archive: LOCAL_AGENT_HOME and LOCAL_AGENT_ARCHIVE point into a temp dir.

The names in the fixtures (Priya, Omar, Dana, Lena, Acme) are invented.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "verify_archive_redaction.py"
ARCHIVE_SPEC = Path(__file__).resolve().parents[2] / "references" / "archive.md"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "redaction"

spec = importlib.util.spec_from_file_location("verify_archive_redaction", SCRIPT)
gate = importlib.util.module_from_spec(spec)
# dataclasses resolve string annotations through sys.modules, so register before exec.
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)

NOTE = (FIXTURES / "note.md").read_text(encoding="utf-8")
REDACTED = (FIXTURES / "redacted.md").read_text(encoding="utf-8")
SOURCE = (FIXTURES / "source.md").read_text(encoding="utf-8")

SLUG = "2026-05-04-pipeline-rollout"
FOLLOW_UP = "**Follow-up:**"
WITHHELD_SENTENCE = (
    "Omar plans to reach out personally afterwards. Noted here only for\n"
    "continuity; never surface in the pre-read, deck, or any message to a third party."
)
NOTICE_BODY = (
    "> 1 block withheld: personnel. Full text stays in the sanctum at\n"
    "> `sessions/redacted/2026-05-04-pipeline-rollout.md`.\n"
)
NOTICE = "> [!warning] Withheld from archive\n" + NOTICE_BODY

# The fixture's withheld block, without the redacted file's provenance header.
REDACTED_BODY = REDACTED.split("\n", 2)[2]

# The same log with nothing withheld: no marked paragraph, no notice, `redacted: false`.
CLEAN_SOURCE = SOURCE.replace(REDACTED_BODY, "")
CLEAN_NOTE = NOTE.replace("redacted: true\nredacted_count: 1\n", "redacted: false\n").replace(
    NOTICE + "\n", ""
)

# Names from the fixture's withheld block. A failure message must print none of them.
WITHHELD_NAMES = ("omar", "priya", "dana")


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one occurrence of {old!r}, found {count}")
    return text.replace(old, new)


def before_follow_up(text: str, addition: str) -> str:
    return replace_once(text, FOLLOW_UP, addition + "\n\n" + FOLLOW_UP)


def line_of(text: str, needle: str) -> int:
    """The 1-based line of the first occurrence of needle, as the gate reports lines."""
    return text[: text.index(needle)].count("\n") + 1


def withheld(body: str, source: str = f"{SLUG}.md") -> str:
    """A redacted file as step 4 of the archive sequence writes it: a provenance header
    naming the source log, then one marked block."""
    return f"## Withheld from {source}\n\n**Confidential:** {body}\n"


def source_for(redacted_text: str) -> str:
    """The source log a redacted file was cut from: the fixture log with its withheld
    block swapped for the redacted file's blocks. The source-completeness check then
    holds by construction, and the check under test is the only thing that can fail."""
    return replace_once(SOURCE, REDACTED_BODY, redacted_text.split("\n", 2)[2])


def renamed(text: str, slug: str) -> str:
    """The fixture note archived from a sanctum log with a different filename: the slug,
    `source:` and the notice path all follow it."""
    return text.replace(SLUG, slug)


class GateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.archive = self.root / "archive"
        self.archive.mkdir()
        self.home = self.root / "home"
        self.sessions = self.home / "_bmad" / "memory" / "local-agent" / "sessions"
        (self.sessions / "redacted").mkdir(parents=True)
        self.env = {
            "LOCAL_AGENT_HOME": str(self.home),
            "LOCAL_AGENT_ARCHIVE": str(self.archive),
        }
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # Tree builders.

    def note(self, text: str = NOTE, slug: str = SLUG, month: str = "2026/05") -> Path:
        path = self.archive / "log" / month / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def redacted(self, text: str = REDACTED, name: str = f"{SLUG}.md") -> Path:
        path = self.sessions / "redacted" / name
        path.write_text(text, encoding="utf-8")
        return path

    def source(self, text: str = SOURCE, name: str = f"{SLUG}.md") -> Path:
        path = self.sessions / name
        path.write_text(text, encoding="utf-8")
        return path

    # Running and asserting.

    def run_gate(self, *args: Path | str) -> tuple[int, str, str]:
        """Call main() in-process with exactly these arguments. Returns (exit code,
        stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = gate.main([str(a) for a in args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def run_entrypoint(self, *args: Path | str) -> tuple[int, str, str]:
        """Call entrypoint(), the CLI's exception boundary, in-process. An unexpected
        exception there becomes exit 2 with a traceback on stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = gate.entrypoint([str(a) for a in args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def gate(
        self, note: Path, redacted: Path | None = None, *extra: str, source: Path | None = None
    ) -> tuple[int, str, str]:
        """Run the gate the way step 7 of the archive sequence does. With no redacted file
        it passes --no-withheld. Without an explicit source it writes the source log the
        redacted file was cut from, under the redacted file's name."""
        if source is None:
            if redacted is None:
                source = self.source(CLEAN_SOURCE)
            else:
                text = source_for(redacted.read_text(encoding="utf-8"))
                source = self.source(text, name=redacted.name)
        head = [redacted] if redacted is not None else ["--no-withheld"]
        return self.run_gate(note, *head, "--source", source, *extra)

    def assert_pass(self, result: tuple[int, str, str]) -> None:
        code, _, err = result
        self.assertEqual(code, 0, err)

    def assert_fail(self, result: tuple[int, str, str], *fragments: str) -> str:
        code, _, err = result
        self.assertEqual(code, 1, err)
        for fragment in fragments:
            self.assertIn(fragment, err)
        self.assertIn("Do not prune", err)
        return err

    def assert_cannot_run(self, result: tuple[int, str, str], fragment: str) -> str:
        code, _, err = result
        self.assertEqual(code, 2, err)
        self.assertIn(fragment, err)
        self.assertNotIn("unexpected error", err)
        return err

    def assert_no_withheld_names(self, err: str) -> None:
        for name in WITHHELD_NAMES:
            self.assertNotIn(name, err.lower())


class ContractTests(GateTestCase):
    def test_clean_archive_with_source_passes(self):
        code, out, err = self.gate(self.note(), self.redacted())
        self.assertEqual(code, 0, err)
        self.assertIn("OK", out)
        self.assertIn("survives", out)

    def test_nothing_withheld_passes_with_no_withheld(self):
        code, out, err = self.gate(self.note(CLEAN_NOTE))
        self.assertEqual(code, 0, err)
        self.assertIn("nothing withheld", out)

    def test_quiet_prints_nothing_on_success(self):
        code, out, err = self.gate(self.note(), self.redacted(), "--quiet")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")

    def test_cli_exit_codes(self):
        # The one subprocess test: the script's own entry point maps a pass, a leak and
        # an unrunnable check to 0, 1 and 2. Everything else runs main() in-process.
        env = {**os.environ, **self.env}
        red, src = self.redacted(), self.source()

        def cli(note: Path, redacted: Path) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(SCRIPT), str(note), str(redacted), "--source", str(src)],
                capture_output=True, text=True, check=False, env=env,
            )

        ok = cli(self.note(), red)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        leak = cli(self.note(before_follow_up(NOTE, WITHHELD_SENTENCE)), red)
        self.assertEqual(leak.returncode, 1, leak.stderr)
        self.assertIn("withheld sentence present", leak.stderr)
        missing = cli(self.note(), red.with_name("nope.md"))
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn("redacted file does not exist", missing.stderr)
        self.assertNotIn("unexpected error", missing.stderr)


class FailClosedTests(GateTestCase):
    def test_unexpected_exception_exits_2(self):
        with mock.patch.object(gate, "main", side_effect=RuntimeError("boom")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(gate.entrypoint([]), 2)
        self.assertIn("cannot run: unexpected error", err.getvalue())

    def test_missing_source_argument_exits_2(self):
        # A note that silently dropped a marked block once passed "verified clean:
        # nothing withheld" because nothing compared it against the log about to be pruned.
        err = self.assert_cannot_run(
            self.run_gate(self.note(CLEAN_NOTE), "--no-withheld"), "--source is required"
        )
        self.assertNotIn("verified clean", err)

    def test_missing_note_exits_2(self):
        self.assert_cannot_run(
            self.gate(self.archive / "missing.md", self.redacted()), "archived note does not exist"
        )

    def test_non_utf8_note_exits_2(self):
        note = self.note()
        note.write_bytes(b"---\ntype: session-log\n\xff\xfe broken\n---\n")
        self.assert_cannot_run(self.gate(note, self.redacted()), "archived note unreadable")

    def test_empty_note_exits_2(self):
        self.assert_cannot_run(self.gate(self.note("\n\n"), self.redacted()), "is empty")

    def test_empty_redacted_file_exits_2(self):
        # An empty redacted file means the withheld material was lost. Passing here
        # would let the source log be pruned, destroying the only remaining copy.
        self.assert_cannot_run(
            self.run_gate(self.note(), self.redacted(""), "--source", self.source()),
            "withheld material was lost",
        )

    def test_empty_source_exits_2(self):
        self.assert_cannot_run(
            self.gate(self.note(), self.redacted(), source=self.source("\n")), "is empty"
        )

    def test_missing_source_file_exits_2(self):
        self.assert_cannot_run(
            self.gate(self.note(), self.redacted(), source=self.sessions / "nope.md"),
            "source log does not exist",
        )

    def test_no_redacted_file_and_no_flag_exits_2(self):
        self.assert_cannot_run(
            self.run_gate(self.note(), "--source", self.source()), "no redacted file given"
        )

    def test_no_withheld_with_an_existing_redacted_file_exits_2(self):
        self.assert_cannot_run(
            self.run_gate(
                self.note(CLEAN_NOTE), self.redacted(), "--no-withheld",
                "--source", self.source(CLEAN_SOURCE),
            ),
            "--no-withheld passed together with the redacted file",
        )

    def test_no_withheld_with_a_missing_redacted_file_exits_2(self):
        # Both arguments say opposite things about the archive. A typo in the redacted
        # path once made the contradiction pass as a clean archive.
        self.assert_cannot_run(
            self.run_gate(
                self.note(CLEAN_NOTE), self.sessions / "redacted" / "typo.md", "--no-withheld",
                "--source", self.source(CLEAN_SOURCE),
            ),
            "--no-withheld passed together with the redacted file",
        )

    def test_redacted_file_outside_sessions_redacted_exits_2(self):
        stray = self.root / "elsewhere" / f"{SLUG}.md"
        stray.parent.mkdir()
        stray.write_text(REDACTED, encoding="utf-8")
        self.assert_cannot_run(self.gate(self.note(), stray), "not under sessions/redacted/")

    def test_shingle_out_of_range_exits_2(self):
        note, red = self.note(), self.redacted()
        for size in (gate.MIN_SHINGLE_WORDS - 1, gate.MAX_SHINGLE_WORDS + 1):
            with self.subTest(size):
                self.assert_cannot_run(
                    self.gate(note, red, "--shingle", str(size)), "--shingle must be between"
                )

    def test_shingle_range_ends_pass(self):
        note, red = self.note(), self.redacted()
        for size in (gate.MIN_SHINGLE_WORDS, gate.MAX_SHINGLE_WORDS):
            with self.subTest(size):
                self.assert_pass(self.gate(note, red, "--shingle", str(size)))

    def test_misconfigured_archive_root_exits_2(self):
        note, red = self.note(), self.redacted()
        with mock.patch.dict(os.environ, {"LOCAL_AGENT_ARCHIVE": str(self.root / "typo")}):
            self.assert_cannot_run(self.gate(note, red), "LOCAL_AGENT_ARCHIVE")


class MessageMaskingTests(GateTestCase):
    """A failure report is read by a model and often pasted onward. It locates each
    problem by line and prints no withheld text, or it becomes a second copy."""

    def test_sentence_leak_reports_the_line_and_no_text(self):
        text = before_follow_up(NOTE, WITHHELD_SENTENCE)
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted()),
            f"withheld sentence present in archived note at line {line_of(text, 'Omar plans')}",
            "text not printed",
        )
        self.assertNotIn("reach out personally", err)
        self.assertNotIn("continuity", err)

    def test_word_run_leak_prints_no_names(self):
        text = before_follow_up(NOTE, "Omar told Dana in this meeting that Priya is leaving.")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted()), "withheld 7-word run present"
        )
        self.assert_no_withheld_names(err)

    def test_token_sweep_prints_lengths_and_lines(self):
        text = replace_once(NOTE, '"[[person/Lena]]"]', '"[[person/Lena]]", "[[person/Priya]]"]')
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted()),
            "tokens from the withheld text appear in the archived note at line 5: <5-char token>",
        )
        self.assert_no_withheld_names(err)

    def test_lost_source_sentence_reports_the_line_and_no_text(self):
        # The source-loss message once printed "...slack omar to" for a dropped block.
        err = self.assert_fail(
            self.gate(self.note(CLEAN_NOTE), source=self.source(SOURCE)),
            f"source sentence at line {line_of(SOURCE, '**Confidential')} of --source",
        )
        self.assert_no_withheld_names(err)

    def test_tainted_filename_is_masked_everywhere_in_the_report(self):
        slug = "2026-05-04-priya-leaving"
        red = self.redacted(name=f"{slug}.md")
        err = self.assert_fail(
            self.gate(self.note(renamed(NOTE, slug), slug=slug), red),
            "filename names a withheld subject",
            "<withheld>",
        )
        self.assert_no_withheld_names(err)

    def test_concatenated_subject_in_the_filename_is_masked(self):
        slug = "2026-05-04-personc-leaving"
        red = self.redacted(withheld("Person C is leaving.", source=f"{slug}.md"), f"{slug}.md")
        err = self.assert_fail(
            self.gate(self.note(renamed(NOTE, slug), slug=slug), red),
            "filename names a withheld subject",
        )
        self.assertNotIn("personc", err.lower())


class SourceCompletenessTests(GateTestCase):
    def test_sentence_lost_from_both_files_fails(self):
        # Partial redaction: the source carries a second marked sentence that reached
        # neither the note nor the redacted file, so the prune would destroy it.
        source = before_follow_up(
            SOURCE, "Omar is on a performance plan until July and nobody else knows."
        )
        self.assert_fail(
            self.gate(self.note(), self.redacted(), source=self.source(source)),
            "in neither the archived note nor the redacted file",
        )

    def test_sentence_lost_without_withheld_material_fails(self):
        source = before_follow_up(CLEAN_SOURCE, "Budget for the pilot is still unresolved.")
        self.assert_fail(
            self.gate(self.note(CLEAN_NOTE), source=self.source(source)),
            "in neither the archived note nor the redacted file",
        )

    def test_more_than_ten_lost_sentences_are_summarised(self):
        lost = " ".join(f"Budget line {n} for the pilot is unresolved." for n in range(13))
        source = before_follow_up(CLEAN_SOURCE, lost)
        err = self.assert_fail(
            self.gate(self.note(CLEAN_NOTE), source=self.source(source)),
            "...and 3 more source sentence(s) missing from both",
        )
        self.assertEqual(err.count("in neither the archived note"), gate.MAX_LOST_LISTED)

    def test_redacted_text_not_verbatim_from_source_fails(self):
        redacted = replace_once(REDACTED, "Priya is leaving", "Priya is departing")
        self.assert_fail(
            self.gate(self.note(), self.redacted(redacted), source=self.source()),
            "redacted file sentence at line 3",
            "not verbatim",
        )

    def test_sentence_level_withholding_passes(self):
        # A referring sentence withheld from a surviving labelled paragraph splits one
        # source line across the two files, which the check must accept.
        source = replace_once(
            SOURCE,
            "**Follow-up:** Draft the rollout plan",
            "**Follow-up:** As per the note at the top, say nothing. Draft the rollout plan",
        )
        redacted = REDACTED + "\nAs per the note at the top, say nothing.\n"
        self.assert_pass(
            self.gate(self.note(), self.redacted(redacted), source=self.source(source))
        )

    def test_source_not_listed_in_frontmatter_fails(self):
        s = self.source(name="2026-05-04-other-log.md")
        self.assert_fail(
            self.gate(self.note(), self.redacted(), source=s), "is not listed under `source`"
        )


class MarkerTests(GateTestCase):
    def test_marker_inside_the_notice_callout_fails(self):
        marked = replace_once(
            NOTE, NOTICE_BODY, NOTICE_BODY + "> **Confidential:** the Acme lease ends in June.\n"
        )
        self.assert_fail(self.gate(self.note(marked), self.redacted()), "confidentiality marker")

    def test_confidential_label_in_the_note_fails(self):
        # The marked block never reached the redacted file, so no text comparison can
        # see it. The marker itself is the evidence.
        text = before_follow_up(NOTE, "**Confidential:** the Acme office lease ends in June.")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "confidentiality marker")

    def test_marker_fails_in_every_position(self):
        cases = {
            "plain prefix": "Confidential: the office lease ends in June.",
            "upper case": "CONFIDENTIAL: the office lease ends in June.",
            "extra spaces": "**Do   not   share:** the office lease ends in June.",
            "blockquote": "> **Private:** the office lease ends in June.",
            "bullet": "- **Sensitive:** the office lease ends in June.",
            "numbered": "1. Internal only: the office lease ends in June.",
            "heading": "### Off the record: lease",
            "code fence": "```\nNDA: the office lease ends in June.\n```",
            "html comment": "<!-- confidential: the office lease ends in June. -->",
            "label only": "**Confidential:**\n\nThe office lease ends in June.",
            "mid sentence": "Omar said this stays between us: the lease ends in June.",
            "keep quiet": "Dana asked me to keep this quiet for now.",
            "told privately": "Omar told me privately that the lease ends in June.",
            "eyes only": "The board pack, marked eyes only, lists three causes.",
            "split across lines": "Dana was clear: do not\nrepeat this outside the room.",
            "curly apostrophe": "She said don\u2019t tell the group before Friday.",
        }
        for label, line in cases.items():
            with self.subTest(label):
                self.assert_fail(
                    self.gate(self.note(before_follow_up(NOTE, line)), self.redacted()),
                    "confidentiality marker",
                )

    def test_marker_phrasings_a_review_carried_past_the_gate(self):
        # Each of these passed as a clean archive: an inflection, a hyphenated or dashed
        # spelling, a contraction, a bare "Secret" label, and a name between "told" and
        # "privately".
        secret = "the Hamburg office closes in June and Priya leaves at the end of the month."
        cases = {
            "confidentially": f"Omar told Dana confidentially that {secret}",
            "confidentiality": f"Under strict confidentiality, {secret}",
            "hyphenated off-the-record": f"Off-the-record: {secret}",
            "hyphenated internal-only": f"**Internal-only:** {secret}",
            "en dash": f"Off\u2013the\u2013record: {secret}",
            "non-breaking hyphen": f"Internal\u2011only: {secret}",
            "don't share": f"Don't share this: {secret}",
            "don't repeat": f"Please don't repeat this: {secret}",
            "secret label": f"**Secret:** {secret}",
            "told a name privately": f"Omar told Dana privately that {secret}",
            "said privately": f"Omar said privately that {secret}",
            "curly apostrophe don't share": f"Don\u2019t share: {secret}",
            "curly apostrophe don't repeat": f"Don\u2019t repeat this: {secret}",
            "modifier apostrophe": f"Don\u02bct share: {secret}",
            "told a cyrillic name privately": (
                f"Omar told \u0414\u0430\u043d\u0430 privately that {secret}"
            ),
        }
        for label, line in cases.items():
            with self.subTest(label):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                err = self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    "confidentiality marker",
                )
                if label.startswith(("told", "said")):
                    self.assertIn(gate.PRIVATELY_LABEL, err)
                    self.assertNotIn("dana", err.lower())

    def test_marker_in_a_frontmatter_value_fails(self):
        text = replace_once(
            CLEAN_NOTE,
            "redacted: false\n",
            'summary: "Confidential, do not share: the Hamburg office closes in June"\n'
            "redacted: false\n",
        )
        self.assert_fail(
            self.gate(self.note(text)),
            f"confidentiality marker 'confidential' in the prefix of line "
            f"{line_of(text, 'summary:')}",
            "in the archived note frontmatter",
        )

    def test_marker_in_a_block_scalar_frontmatter_value_fails(self):
        text = replace_once(
            CLEAN_NOTE,
            "redacted: false\n",
            "summary: >-\n  The rollout went fine. Keep this quiet\n  until the town hall.\n"
            "redacted: false\n",
        )
        self.assert_fail(self.gate(self.note(text)), "'keep this quiet' in the archived note")

    def test_marker_fails_even_when_nothing_was_declared_withheld(self):
        text = before_follow_up(CLEAN_NOTE, "**Confidential:** the lease ends in June.")
        source = before_follow_up(CLEAN_SOURCE, "**Confidential:** the lease ends in June.")
        self.assert_fail(
            self.gate(self.note(text), source=self.source(source)), "confidentiality marker"
        )

    def test_frontmatter_key_is_a_prefix_label(self):
        # The marked paragraph moved into a property whose key is the marker. Its words
        # sit in the note, so the source check passes, and only the key gives it away.
        secret = "The Zephyr acquisition closes Friday."
        # label: (frontmatter field, the line carrying the marker key)
        cases = {
            "confidential": (f"confidential: {secret}\n", "confidential:"),
            "private": (f"private: {secret}\n", "private:"),
            "sensitive": (f"sensitive: {secret}\n", "sensitive:"),
            "secret": (f"secret: {secret}\n", "secret:"),
            "quoted key": (f'"private": "{secret}"\n', '"private":'),
            "single-quoted key": (f"'confidential': {secret}\n", "'confidential':"),
            "nested mapping": (f"notes:\n  confidential: {secret}\n", "  confidential:"),
            "block scalar": (f"sensitive: >-\n  {secret}\n", "sensitive: >-"),
            "list of mappings": (f"notes:\n  - private: {secret}\n", "  - private:"),
        }
        for label, (field, key_line) in cases.items():
            with self.subTest(label):
                text = replace_once(CLEAN_NOTE, "redacted: false\n", "redacted: false\n" + field)
                source = before_follow_up(CLEAN_SOURCE, f"**Confidential:** {secret}")
                self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    f"in the prefix of line {line_of(text, key_line)}",
                )

    def test_marker_heading_marks_its_section(self):
        secret = "The Zephyr acquisition closes Friday."
        for heading in (
            "## Private", "## Sensitive", "## Secret", "### Confidential", "> ## Private",
            "## Private ##", "## Internal-only",
        ):
            with self.subTest(heading):
                block = f"{heading}\n\n{secret}"
                text = before_follow_up(CLEAN_NOTE, block)
                source = before_follow_up(CLEAN_SOURCE, block)
                self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    f"in the prefix of line {line_of(text, heading)}",
                )

    def test_setext_heading_marks_its_section(self):
        secret = "The Zephyr acquisition closes Friday."
        headings = ("Private\n-------", "Sensitive\n=========", "Secret\n---", "Confidential\n===")
        for heading in headings:
            with self.subTest(heading):
                block = f"{heading}\n\n{secret}"
                text = before_follow_up(CLEAN_NOTE, block)
                source = before_follow_up(CLEAN_SOURCE, block)
                self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    f"in the prefix of line {line_of(text, heading)}",
                )

    def test_public_setext_headings_pass(self):
        for heading in ("Linked\n------", "Key outcomes\n============", "Next steps\n---"):
            with self.subTest(heading):
                block = f"{heading}\n\nLena owns the next step."
                text = before_follow_up(NOTE, block)
                source = before_follow_up(SOURCE, block)
                self.assert_pass(
                    self.gate(self.note(text), self.redacted(), source=self.source(source))
                )

    def test_public_headings_pass(self):
        for heading in (
            "## Linked", "## Key outcomes", "### Decisions", "## Rollout plan, phase two",
            "## Sensitivity analysis", "## Secrets manager migration",
        ):
            with self.subTest(heading):
                block = f"{heading}\n\nLena owns the next step."
                text = before_follow_up(NOTE, block)
                source = before_follow_up(SOURCE, block)
                self.assert_pass(
                    self.gate(self.note(text), self.redacted(), source=self.source(source))
                )

    def test_prefix_only_words_mid_sentence_are_not_markers(self):
        text = before_follow_up(
            NOTE,
            "Lena moved the private repo and the sensitive-data scanner to the new org, "
            "and rotated the client secret.",
        )
        self.assert_pass(self.gate(self.note(text), self.redacted()))

    def test_prefix_only_words_inside_config_keys_are_not_markers(self):
        # `client_secret:` and `private_key_path:` are identifiers. The marker rule reads
        # a prefix-only word only when it stands as a word of its own.
        for line in (
            "client_secret: null",
            "private_key_path: /etc/deploy/key",
            "sensitive_data_scan: enabled for acme-gateway",
        ):
            with self.subTest(line):
                text = before_follow_up(NOTE, line)
                source = before_follow_up(SOURCE, line)
                self.assert_pass(
                    self.gate(self.note(text), self.redacted(), source=self.source(source))
                )

    def test_marker_list_matches_the_specification(self):
        # references/archive.md section 1 prints the list a model reads; PREFIX_MARKERS
        # is the list the script enforces. A marker in one and not the other is either an
        # unenforced rule or an undocumented one.
        spec_text = ARCHIVE_SPEC.read_text(encoding="utf-8")
        start = spec_text.index("matched case-insensitively:")
        paragraph = spec_text[start:].split("\n\n", 2)[1]
        self.assertEqual(re.findall(r"`([^`]+)`", paragraph), list(gate.PREFIX_MARKERS))


# Credential-shaped test values are assembled at runtime so no literal in this file
# matches a real token format; secret scanners on the hosting side reject pushes that
# carry one, fake or not.
def fake(prefix: str, body: str) -> str:
    return prefix + body


class CredentialShapeTests(GateTestCase):
    GHP = fake("gh" + "p_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")

    NAMED_SHAPES: ClassVar[dict[str, tuple[str, str]]] = {
        "slack token": ("", fake("xo" + "xb-", "1234567890-abcdefghijklmnop")),
        "slack webhook": (
            "https://hooks.slack" + ".com/services/",
            fake("T0123ABCD/", "B0123ABCD/abcdefghijklmnopqrstuvwx"),
        ),
        "github fine-grained token": (
            "", fake("github" + "_pat_", "11ABCDEFG0123456789_abcdefghijklmnop")
        ),
        "gitlab token": ("", fake("gl" + "pat-", "abcdefghij0123456789")),
        "openai or anthropic key": ("", fake("s" + "k-", "abcdefghijklmnopqrstuvwxyz0123")),
        "stripe key": ("", fake("s" + "k_live_", "51HxAbCdEfGhIjKlMnOpQrSt")),
        "npm token": ("", fake("np" + "m_", "abcdefghijklmnopqrstuvwxyz0123456789")),
        "pypi token": ("", fake("py" + "pi-", "AgEIcHlwaS5vcmcCJGFiY2RlZmdoaWprbG1ub3Bx")),
        "aws access key": ("", fake("AK" + "IA", "IOSFODNN7EXAMPLE")),
        "aws session key id": ("", fake("AS" + "IA", "IOSFODNN7EXAMPLE")),
        "aws secret access key": (
            "aws_secret_access_key = ", fake("wJalrXUtnFEMI/K7MDENG/", "bPxRfiCYEXAMPLEKEY")
        ),
        "google api key": ("", fake("AI" + "za", "SyA-abcdefghijklmnopqrstuvwxyz01234")),
        "jwt": (
            "",
            fake(
                "ey" + "JhbGciOiJIUzI1NiJ9.",
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            ),
        ),
        "private key block": ("", fake("-----BEGIN RSA ", "PRIVATE KEY-----")),
        "bearer token": ("Bearer ", fake("abcdefghij", "klmnopqrstuvwxyz0123")),
        "basic auth header": ("Authorization: Basic ", fake("YWRtaW46", "aHVudGVyMg==")),
        "url with embedded password": (
            "postgres://admin:", fake("S3cret", "Passw0rd") + "@db.prod.internal:5432/app"
        ),
        "password assignment": ("Staging admin password: ", fake("Tr0ub4", "dor&3-horse")),
        "password alone on its line": ("password: ", fake("correct", "horsebattery")),
        "passwd env": ("DB_PASSWD=", fake("hun", "ter22")),
        "pwd label": ("pwd: ", fake("s3cr", "et!x")),
        "passcode": ("**Passcode:** ", fake("8812", "-alpha")),
        "secret assignment": ("secret = ", fake("abcd", "efgh")),
        "api_key assignment": ('API_KEY="', fake("abcd", "1234") + '"'),
        "client_secret assignment": ("client_secret: ", fake("x9Y8", "z7W6v5")),
        "cookie header": ("cookie: ", fake("session=", "abcdefghijklmnopqrstuvwx")),
        "oauth token or credentials file path": (
            "", "~/.config/acme/application_default_credentials.json"
        ),
        "multiword passphrase": ("Password: ", fake("correct horse ", "battery staple")),
        "json basic auth header": (
            '{"Authorization": "Basic ', fake("dXNlcjpw", "YXNzd29yZA==") + '"}'
        ),
        "json password": ('{"password": "', fake("hunter", "22x") + '"}'),
        "json cookie header": ('{"Cookie": "', fake("session=", "abcdefghijklmnop") + '"}'),
        "yaml folded password": ("password: >-\n  ", fake("aBcdEF", "123456789")),
        "yaml literal api_key": ("api_key: |\n  ", fake("abcd", "1234efgh")),
        "api_key quoted null string": ('api_key: "', fake("nu", "ll") + '"'),
        "env reference with a default": (
            'password = "${DATABASE_PASSWORD:-', fake("hunter", "22") + '}"'
        ),
    }

    PROSE_LINES = (
        "Lena rotated the CI token and the deploy key on Friday.",
        "Password: reset by IT on Friday after the audit.",
        "Password: stored in the team vault.",
        "Password: rotated on Friday, see the runbook.",
        "password: none",
        "Password: changed on 2026-05-04",
        "Rotation: the client secret was replaced on Friday.",
        "Clone with git@github.com:acme/acme-gateway.git as usual.",
        "Docs live at https://docs.example.com/guide for reference.",
    )

    LABELLED_VALUES = (
        "password: reset-6B4p!8Qz",
        '{"password": "reset-6B4p!8Qz"}',
        'password: "none"',
        'password: "null"',
        "Password: my battery horse staple",
        "PIN: 1234",
        "**PIN:** 4821",
        "pin = 0000",
        "token: abc123",
        "access_token = abcdef",
        "API key: hunter22",
        "password: >- # production\n  aBcdEF123456789",
        "password: |2-\n  aBcdEF123456789",
        "password: |-2\n  aBcdEF123456789",
        "password: |-\n  null",
        "password:\n  aBcdEF123456789",
        '{"api_key": null, "password": "hunter22"}',
        "password: none, actually hunter22",
        "Password: rotated",
        "Password: changed to Hunter2024!",
        "PIN: reset to 4821",
        "token: regenerated with abc123xyz",
        "Password: rotated from hunter22 last week",
    )

    WHOLE_VALUE_CREDENTIALS = (
        "password: _null",
        "password: ___",
        'PASSWORD="${DATABASE_PASSWORD}"hunter2',
        'PASSWORD=""hunter2',
        'PASSWORD=""null',
        'PASSWORD="null"x',
        "PASSWORD=$DATABASE_PASSWORD'x'",
        "password: null hunter2",
        "password: **hunter2**",
        '{"api_key": nullx}',
    )

    PUNCTUATED_EXEMPTIONS = (
        'PASSWORD="${DATABASE_PASSWORD}" # loaded by the shell',
        "password: _none_",
        "**Password:** null",
        '{"api_key": null }',
        "api_key: ~;",
    )

    SYNTAX_ONLY_VALUES = (
        "api_key: null",
        "api_key: ~",
        'api_key: ""',
        'password = "${DATABASE_PASSWORD}"',
        "PASSWORD=$DATABASE_PASSWORD",
        'client_secret: "${OAUTH_CLIENT_SECRET}",',
        "api_key: null # deliberately unset",
        "api_key: Null",
        '{"api_key": null}',
        '{"api_key": ""}',
        '{"password": "${DATABASE_PASSWORD}"}',
        "password = None",
        "password: <your-database-password>",
        "The CI variable is secret=${CLIENT_SECRET}.",
        "private_key: null",
        "The SQL fragment is WHERE password IS NULL.",
    )

    def test_github_token_in_note_fails_without_echoing_it(self):
        text = before_follow_up(NOTE, f"The CI token is {self.GHP} for now.")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted()), "credential-shaped value (github"
        )
        self.assertNotIn(self.GHP, err)
        self.assertNotIn(self.GHP[4:20], err)

    def test_every_named_shape_fails_without_echoing_it(self):
        # label: (line as written in the note, the secret part of it)
        cases = self.NAMED_SHAPES
        for label, (lead, secret) in cases.items():
            with self.subTest(label):
                line = f"Recorded here:\n{lead}{secret}"
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                err = self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    f"credential-shaped value ({self.shape_for(label)}) on line "
                    f"{line_of(text, 'Recorded here') + 1}",
                )
                self.assertNotIn(secret.strip('"{}'), err)

    @staticmethod
    def shape_for(label: str) -> str:
        """The shape name a case reports. Several labelled assignments share one shape."""
        if label == "aws session key id":
            return "aws access key"
        if label.startswith(("json basic", "json cookie")):
            return label.removeprefix("json ")
        assignments = (
            "password", "passwd", "pwd", "passcode", "secret", "api_key", "client_secret",
            "multiword", "json password", "yaml", "env reference",
        )
        return gate.CREDENTIAL_LABEL_SHAPE if label.startswith(assignments) else label

    def test_prose_about_credentials_is_not_a_credential(self):
        lines = self.PROSE_LINES
        for line in lines:
            with self.subTest(line):
                text = before_follow_up(NOTE, line)
                self.assert_pass(self.gate(self.note(text), self.redacted()))

    def test_every_value_after_a_credential_label_is_a_credential(self):
        # Each of these archived as clean: a status word glued to a password, a quoted
        # "none", a passphrase with a short first word, a PIN, and YAML block scalars in
        # every legal spelling, including one holding the string "null".
        cases = self.LABELLED_VALUES
        for line in cases:
            with self.subTest(line):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                err = self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    f"credential-shaped value ({gate.CREDENTIAL_LABEL_SHAPE}) on line "
                    f"{line_of(text, line.split(chr(10))[0])}",
                )
                self.assertNotIn("hunter22", err)
                self.assertNotIn("aBcdEF", err)

    def test_exemptions_hold_only_for_the_whole_value(self):
        # An underscore is a literal character, and text glued to a quoted placeholder
        # or an empty quoted string is shell concatenation that assigns it.
        cases = self.WHOLE_VALUE_CREDENTIALS
        for line in cases:
            with self.subTest(line):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)),
                    f"({gate.CREDENTIAL_LABEL_SHAPE})",
                )

    def test_exempt_values_with_permitted_punctuation_pass(self):
        for line in self.PUNCTUATED_EXEMPTIONS:
            with self.subTest(line):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                self.assert_pass(self.gate(self.note(text), source=self.source(source)))

    def test_quoted_yaml_keys_get_a_verdict(self):
        # A quoted key outside any `{...}` once made the classifier expect a delimiter
        # that was not there, and the gate crashed with exit 2. A `${...}` reference's
        # closing brace was also read as the end of a flow mapping.
        cases = {
            '"password": null': 0,
            "'password': None": 0,
            '"api_key": ${API_KEY}': 0,
            '"api_key": ""': 0,
            '{"api_key": ${API_KEY}}': 0,
            "Example {stage} password: null": 0,
            '"password": hunter22x': 1,
            "'api_key': hunter22x": 1,
            '{"api_key": ${API_KEY}x}': 1,
            '"api_key": ${API_KEY}x': 1,
        }
        for line, expected in cases.items():
            with self.subTest(line):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                code, _, err = self.run_entrypoint(
                    self.note(text), "--no-withheld", "--source", self.source(source)
                )
                self.assertEqual(code, expected, err)
                self.assertNotIn("Traceback", err)

    def test_credential_rules_apply_inside_fenced_code(self):
        block = "```yaml\ndatabase:\n  password: >-\n    aBcdEF123456789\n  pin: 1234\n```"
        text = before_follow_up(CLEAN_NOTE, block)
        source = before_follow_up(CLEAN_SOURCE, block)
        err = self.assert_fail(self.gate(self.note(text), source=self.source(source)))
        self.assertEqual(err.count(f"({gate.CREDENTIAL_LABEL_SHAPE})"), 2)

    def test_multiline_basic_auth_header_is_caught(self):
        line = "Authorization:\n  Basic " + fake("dXNlcjpw", "YXNzd29yZA==")
        text = before_follow_up(CLEAN_NOTE, line)
        source = before_follow_up(CLEAN_SOURCE, line)
        self.assert_fail(
            self.gate(self.note(text), source=self.source(source)),
            "credential-shaped value (basic auth header)",
        )

    def test_syntax_only_values_are_not_credentials(self):
        # A typed null and a bare environment variable reference name where a secret
        # lives without holding one. --allow cannot clear a credential refusal, so a false
        # positive here would make an accurate configuration excerpt unarchivable.
        lines = self.SYNTAX_ONLY_VALUES
        for line in lines:
            with self.subTest(line):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                self.assert_pass(self.gate(self.note(text), source=self.source(source)))

    def test_shape_in_frontmatter_fails(self):
        text = replace_once(NOTE, "redacted: true\n", f"token: {self.GHP}\nredacted: true\n")
        err = self.assert_fail(self.gate(self.note(text), self.redacted()), "on line 8")
        self.assertNotIn(self.GHP, err)

    def test_withheld_credential_value_is_not_echoed(self):
        # The template once printed the matched value in the failure line, which made the
        # report a second copy of the secret.
        redacted = REDACTED + "\nAccess PIN: 482913.\n"
        text = before_follow_up(NOTE, "PIN: 482913")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted(redacted)),
            "credential-shaped value from the withheld text",
            "value not printed",
        )
        self.assertNotIn("482913", err)

    def test_leaked_digit_token_is_described_by_length(self):
        redacted = REDACTED + "\nSeverance agreed at 185000 for Priya.\n"
        text = before_follow_up(NOTE, "Budget line 185000 is closed.")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted(redacted)), "<6-char token>"
        )
        self.assertNotIn("185000", err)


class NormalizationTests(GateTestCase):
    def test_homoglyph_and_zero_width_sentence_is_caught(self):
        # One Cyrillic vowel and one zero-width space in the note copy defeated both the
        # exact sentence match and every word run before normalisation folded them away.
        redacted = REDACTED + "\nPriya told Omar she is leaving in June.\n"
        disguised = "Priya told Om\u0430r she is lea\u200bving in June."
        self.assert_fail(
            self.gate(self.note(before_follow_up(NOTE, disguised)), self.redacted(redacted)),
            "withheld sentence present",
        )

    def test_homoglyph_and_zero_width_word_is_caught(self):
        disguised = "Lena thanked Pr\u200bi\u0443a for the review."
        text = before_follow_up(NOTE, disguised)
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted()),
            f"tokens from the withheld text appear in the archived note at line "
            f"{line_of(text, 'Lena thanked')}: <5-char token>",
        )
        self.assert_no_withheld_names(err)

    def test_diacritics_do_not_hide_a_name(self):
        redacted = REDACTED + "\nJos\u00e9 is being moved off the team.\n"
        text = before_follow_up(NOTE, "Jose reviewed the plan.")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted(redacted)),
            f"at line {line_of(text, 'Jose reviewed')}: <4-char token>",
        )
        self.assertNotIn("jose", err.lower())

    def test_cyrillic_withheld_sentence_is_caught(self):
        # An ASCII word pattern sees nothing in Cyrillic, so this passed every check.
        sentence = (
            "\u041e\u043b\u044c\u0433\u0430 \u0443\u0445\u043e\u0434\u0438\u0442 "
            "\u0438\u0437 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438 \u0432 "
            "\u043a\u043e\u043d\u0446\u0435 \u043c\u0435\u0441\u044f\u0446\u0430."
        )
        redacted = REDACTED + "\n" + sentence + "\n"
        self.assert_fail(
            self.gate(self.note(before_follow_up(NOTE, sentence)), self.redacted(redacted)),
            "withheld sentence present",
        )

    def test_cyrillic_text_that_was_not_withheld_passes(self):
        sentence = (
            "\u041a\u043e\u043c\u0430\u043d\u0434\u0430 \u043e\u0431\u0441\u0443\u0434"
            "\u0438\u043b\u0430 \u043f\u043b\u0430\u043d \u0440\u0435\u043b\u0438\u0437"
            "\u0430."
        )
        source = before_follow_up(SOURCE, sentence)
        text = before_follow_up(NOTE, sentence)
        self.assert_pass(self.gate(self.note(text), self.redacted(), source=self.source(source)))

    def test_bom_and_crlf_do_not_hide_a_tainted_source_line(self):
        # A byte-order mark once made the frontmatter parse miss, and the source line
        # check was skipped without a word.
        text = replace_once(
            NOTE, f"source: sessions/{SLUG}.md", "source: sessions/2026-05-04-priya-leaving.md"
        )
        text = "\ufeff" + text.replace("\n", "\r\n")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted(name="2026-05-04-priya-leaving.md")),
            "provenance field names a withheld subject in 'sessions/2026-05-04-<withheld>-",
        )
        self.assert_no_withheld_names(err)

    def test_bom_and_crlf_on_a_clean_archive_pass(self):
        note = self.note("\ufeff" + NOTE.replace("\n", "\r\n"))
        source = self.source("\ufeff" + SOURCE.replace("\n", "\r\n"))
        self.assert_pass(self.gate(note, self.redacted(), source=source))


class SentenceVariantTests(GateTestCase):
    def test_verbatim_withheld_sentence_is_caught(self):
        sentence = "Person A told Person B that Person C is leaving the company this quarter."
        redacted = self.redacted(withheld(sentence))
        self.assert_fail(
            self.gate(self.note(before_follow_up(NOTE, sentence)), redacted),
            "withheld sentence present",
        )

    def test_short_withheld_sentence_is_caught(self):
        # A withheld sentence under the length floor once passed both the sentence check
        # and the word-run check, so a termination and a severance figure archived
        # verbatim. The token sweep is what closes this.
        redacted = self.redacted(withheld("PersonC was fired. Severance: 185000."))
        text = before_follow_up(NOTE, "PersonC was fired. Severance: 185000.")
        self.assert_fail(self.gate(self.note(text), redacted), "tokens from the withheld")

    def test_stripped_label_and_punctuation_do_not_hide_a_short_leak(self):
        # Six words after the label sit below the shingle size. The whole-sentence match
        # was defeated by dropping the label, the comma, the period and by a curly
        # apostrophe.
        redacted = REDACTED + "\n**Confidential:** Priya's resignation, effective June thirtieth.\n"
        text = before_follow_up(NOTE, "Priya\u2019s resignation effective June thirtieth")
        self.assert_fail(
            self.gate(self.note(text), self.redacted(redacted)), "withheld sentence present"
        )

    def test_sentence_split_across_lines_and_emphasis_is_caught(self):
        text = before_follow_up(
            NOTE, "Omar plans to *reach out*\npersonally afterwards, noted here only for"
        )
        self.assert_fail(
            self.gate(self.note(text), self.redacted()),
            f"withheld 7-word run present in archived note at line {line_of(text, 'Omar plans')}",
        )

    def test_short_common_phrase_shared_with_withheld_text_passes(self):
        text = before_follow_up(NOTE, "Lena asked for the draft by Friday.")
        self.assert_pass(self.gate(self.note(text), self.redacted()))

    def test_date_in_withheld_prose_does_not_fail_on_the_year(self):
        # The note's own date is public by construction, so a withheld sentence naming a
        # day of the same year must not fail every archive on "2026" alone.
        redacted = REDACTED + "\nPriya's last day is 2026-05-30.\n"
        self.assert_pass(self.gate(self.note(), self.redacted(redacted)))

    def test_allow_permits_a_genuine_collision(self):
        # The sweep is deliberately biased toward flagging, so an escape hatch has to exist
        # or a legitimate shared word makes an archive permanently unrunnable.
        text = before_follow_up(NOTE, "PersonC was fired.")
        note = self.note(text)
        redacted = self.redacted(withheld("PersonC was fired."))
        self.assert_fail(self.gate(note, redacted), "tokens from the withheld")
        self.assert_pass(self.gate(note, redacted, "--allow", "personc", "--allow", "fired"))


class FrontmatterSweepTests(GateTestCase):
    def test_entity_named_only_in_withheld_block_is_caught(self):
        # The leak this capability's own documentation calls the subtlest it can produce:
        # the body shows nothing while the frontmatter announces who the withheld block
        # was about. Only `source:` used to be checked.
        redacted = self.redacted(withheld("PersonD is being managed out after a review."))
        text = replace_once(NOTE, '"[[person/Lena]]"]', '"[[person/Lena]]", "[[person/PersonD]]"]')
        self.assert_fail(
            self.gate(self.note(text), redacted),
            "tokens from the withheld text appear in the archived note at line 5",
        )

    def test_withheld_words_in_structural_keys_are_caught(self):
        # `tags`, `type`, `date` and `source_withheld` were dropped from the sweep
        # whatever they held, so each of these archived the subject of the redaction.
        flag = "redacted: true\n"
        cases = {
            "tags": (flag, "tags: [priya, omar, resignation, leaving]\n" + flag),
            "block list tags": (flag, "tags:\n  - rollout\n  - priya\n" + flag),
            "type": ("type: session-log", "type: priya-leaving-end-of-month"),
            "date suffix": ("date: 2026-05-04", "date: 2026-05-04 priya leaving"),
            "source_withheld value": (flag, "source_withheld: priya-leaving\n" + flag),
            "redacted value": ("redacted: true", "redacted: true priya"),
            "redacted_count value": ("redacted_count: 1", "redacted_count: 1 for priya"),
        }
        for label, (old, new) in cases.items():
            with self.subTest(label):
                text = replace_once(NOTE, old, new)
                err = self.assert_fail(
                    self.gate(self.note(text), self.redacted()),
                    "tokens from the withheld text appear in the archived note at line",
                )
                self.assert_no_withheld_names(err)

    def test_structural_keys_with_their_exact_shape_pass(self):
        text = replace_once(
            NOTE, "redacted: true\n", "tags: [rollout, coverage]\nredacted: true\n"
        )
        self.assert_pass(self.gate(self.note(text), self.redacted()))


def quoted_key_variants(line: str) -> list[str]:
    """A `key: value` or `key=value` line in its double-quoted, single-quoted and
    flow-mapping spellings."""
    match = re.match(r"(\w+)(\s*[:=].*)$", line)
    if not match:
        return []
    key, rest = match.groups()
    return [f'"{key}"{rest}', f"'{key}'{rest}", f'{{"{key}"{rest}}}', f"[{key}{rest}]"]


class RobustnessTests(GateTestCase):
    def test_no_corpus_case_crashes_the_gate(self):
        # Every body line the suite uses, every fixture line, and the quoted-key and
        # flow spellings of each, through the CLI's exception boundary. A crash is exit
        # 2 with a traceback, which blocks the prune but hides the real verdict.
        shapes = CredentialShapeTests
        corpus = [lead + secret for lead, secret in shapes.NAMED_SHAPES.values()]
        for group in (
            shapes.PROSE_LINES, shapes.LABELLED_VALUES, shapes.WHOLE_VALUE_CREDENTIALS,
            shapes.PUNCTUATED_EXEMPTIONS, shapes.SYNTAX_ONLY_VALUES,
        ):
            corpus += list(group)
        for fixture in (NOTE, SOURCE, REDACTED):
            corpus += [line for line in fixture.split("\n") if line.strip()]
        corpus += [variant for line in list(corpus) for variant in quoted_key_variants(line)]
        self.assertGreater(len(corpus), 200)
        for line in corpus:
            with self.subTest(line):
                text = before_follow_up(CLEAN_NOTE, line)
                source = before_follow_up(CLEAN_SOURCE, line)
                code, _, err = self.run_entrypoint(
                    self.note(text), "--no-withheld", "--source", self.source(source)
                )
                self.assertIn(code, (0, 1), err)
                self.assertNotIn("Traceback", err)


class FrontmatterSchemaTests(GateTestCase):
    """Archive notes carry only the keys references/archive.md lists, each in a fixed
    shape. Anything the gate does not read in full is refused."""

    def test_unsupported_syntax_and_keys_fail(self):
        secret = "The Zephyr acquisition closes Friday."
        cases = {
            "flow mapping private": f"metadata: {{public: ok, private: {secret}}}\n",
            "flow mapping secret": f"metadata: {{public: ok, secret: {secret}}}\n",
            "flow mapping confidential": f"metadata: {{public: ok, confidential: {secret}}}\n",
            "nested mapping": f"metadata:\n  private: {secret}\n",
            "block scalar": f"private: >-\n  {secret}\n",
            "unknown key": f"summary: {secret}\n",
            "comment": "# reviewed by Lena\n",
            "anchor": "themes: &t [\"[[theme/test-coverage]]\"]\n",
            "alias": "repos: *t\n",
            "tag": "redacted_count: !!str 0\n",
            "quoted key": f'"summary": "{secret}"\n',
            "trailing comment": "type: session-log # archived\n",
            "capitalised boolean": "source_withheld: True\n",
            "people outside the link shape": "people: [Priya]\n",
            "duplicate key": "type: session-log\n",
            "indented list item": "tags:\n    - rollout\n",
        }
        for label, field in cases.items():
            with self.subTest(label):
                text = replace_once(CLEAN_NOTE, "redacted: false\n", "redacted: false\n" + field)
                source = before_follow_up(CLEAN_SOURCE, f"**Private:** {secret}")
                err = self.assert_fail(
                    self.gate(self.note(text), source=self.source(source)), "frontmatter"
                )
                self.assertRegex(
                    err, r"unsupported frontmatter (?:syntax|key)|appears twice"
                )

    def test_escaped_withheld_name_is_refused(self):
        # A YAML reader decodes these to "Zephyr", the person named only in the withheld
        # block, while the gate reads the escape raw. Backslashes are refused outright.
        red = self.redacted(withheld("Zephyr departs Friday."))
        people = 'people: ["[[person/Lena]]"]'
        cases = {
            "unicode escape, flow list": r'people: ["[[person/\u005Aephyr]]"]',
            "hex escape, flow list": r'people: ["[[person/\x5Aephyr]]"]',
            "unicode escape, block list": 'people:\n  - "[[person/\\u005Aephyr]]"',
            "hex escape, block list": 'people:\n  - "[[person/\\x5Aephyr]]"',
            "escaped quote in a tag": 'people: ["[[person/Lena]]"]\ntags: ["a\\"b"]',
        }
        for label, new in cases.items():
            with self.subTest(label):
                err = self.assert_fail(
                    self.gate(self.note(replace_once(NOTE, people, new)), red),
                    "backslash escapes are refused",
                )
                self.assert_no_withheld_names(err)
                self.assertNotIn("zephyr", err.lower())

    def test_double_quoted_scalars_pass(self):
        cases = {
            "quoted type": ("type: session-log", 'type: "session-log"'),
            "quoted date": ("date: 2026-05-04", 'date: "2026-05-04"'),
            "quoted source": (f"source: sessions/{SLUG}.md", f'source: "sessions/{SLUG}.md"'),
            "quoted flow tags": ("redacted: true", 'tags: ["work", "public"]\nredacted: true'),
            "quoted block tags": (
                "redacted: true", 'tags:\n  - "work"\n  - "public"\nredacted: true'
            ),
        }
        for label, (old, new) in cases.items():
            with self.subTest(label):
                note = self.note(replace_once(NOTE, old, new))
                self.assert_pass(self.gate(note, self.redacted()))

    def test_quoting_that_changes_a_type_is_refused(self):
        # A quoted boolean or count is a string to a YAML reader, and an unquoted
        # wikilink is a nested list.
        cases = {
            "quoted boolean": ("redacted: true", 'redacted: "true"'),
            "quoted count": ("redacted_count: 1", 'redacted_count: "1"'),
            "unquoted wikilink": ('people: ["[[person/Lena]]"]', "people: [[[person/Lena]]]"),
            "unquoted block wikilink": (
                'people: ["[[person/Lena]]"]', "people:\n  - [[person/Lena]]"
            ),
        }
        for label, (old, new) in cases.items():
            with self.subTest(label):
                self.assert_fail(
                    self.gate(self.note(replace_once(NOTE, old, new)), self.redacted()),
                    "unsupported frontmatter syntax",
                )

    def test_documented_shapes_pass(self):
        cases = {
            "block lists": (
                '"[[person/Lena]]"]',
                '"[[person/Lena]]"]\ntags:\n  - rollout\n  - coverage',
            ),
            "block list of people": (
                'people: ["[[person/Lena]]"]', 'people:\n  - "[[person/Lena]]"'
            ),
            "empty list": ('repos: ["[[repo/acme-gateway]]"]', "repos: []"),
            "flow list of sources": (
                f"source: sessions/{SLUG}.md",
                f"source: [sessions/{SLUG}.md, sessions/2026-05-04-other-log.md]",
            ),
        }
        for label, (old, new) in cases.items():
            with self.subTest(label):
                text = replace_once(NOTE, old, new)
                self.assert_pass(self.gate(self.note(text), self.redacted()))


class StructureTests(GateTestCase):
    def test_missing_or_unterminated_frontmatter_fails(self):
        for text in (NOTE.split("---\n", 2)[2], NOTE.replace("---\n# Pipeline", "# Pipeline")):
            with self.subTest(text[:12]):
                self.assert_fail(
                    self.gate(self.note(text), self.redacted()), "no closed frontmatter"
                )

    def test_missing_date_fails(self):
        text = replace_once(NOTE, "date: 2026-05-04\n", "")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "no valid `date")

    def test_impossible_date_fails(self):
        text = replace_once(NOTE, "date: 2026-05-04", "date: 2026-02-30")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "no valid `date")

    def test_wrong_month_directory_fails(self):
        self.assert_fail(
            self.gate(self.note(month="2026/06"), self.redacted()), "not under log/2026/05/"
        )

    def test_note_outside_the_configured_archive_fails(self):
        other = self.root / "elsewhere" / "log" / "2026" / "05" / f"{SLUG}.md"
        other.parent.mkdir(parents=True)
        other.write_text(NOTE, encoding="utf-8")
        self.assert_fail(self.gate(other, self.redacted()), "not inside the configured archive")

    def test_slug_date_disagreeing_with_frontmatter_fails(self):
        self.assert_fail(
            self.gate(self.note(slug="2026-05-05-pipeline-rollout"), self.redacted()),
            "does not start with date 2026-05-04",
        )

    def test_source_and_source_withheld_together_fails(self):
        text = replace_once(NOTE, "redacted: true\n", "source_withheld: true\nredacted: true\n")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "carries both `source`")

    def test_neither_source_nor_source_withheld_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md\n", "")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "carries neither")

    def test_source_outside_sessions_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", f"source: notes/{SLUG}.md")
        self.assert_fail(
            self.gate(self.note(text), self.redacted()), "is not a `sessions/<name>.md` path"
        )

    def test_source_with_another_date_fails(self):
        text = replace_once(
            NOTE, f"source: sessions/{SLUG}.md", "source: sessions/2026-05-05-pipeline-rollout.md"
        )
        self.assert_fail(
            self.gate(self.note(text), self.redacted()),
            "`source: sessions/2026-05-05-pipeline-rollout.md` does not carry the note's date",
        )

    def test_redacted_file_with_another_date_fails(self):
        r = self.redacted(name="2026-05-05-pipeline-rollout.md")
        self.assert_fail(
            self.gate(self.note(), r),
            "redacted file '2026-05-05-pipeline-rollout.md' does not carry the note's date",
        )

    def test_slug_not_matching_any_source_fails(self):
        text = NOTE.replace(SLUG, "2026-05-04-other-log")
        r = self.redacted(name="2026-05-04-other-log.md")
        self.assert_fail(self.gate(self.note(text), r), "matches none of its source filenames")

    def test_redacted_file_named_after_a_different_source_fails(self):
        r = self.redacted(name="2026-05-04-other-log.md")
        self.assert_fail(self.gate(self.note(), r), "is not named after any `source`")

    def test_reslugged_archive_with_source_withheld_passes(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", "source_withheld: true")
        text = replace_once(
            text,
            NOTICE_BODY,
            "> 1 block withheld: personnel. Full text stays in the sanctum.\n"
            "> Source date: 2026-05-04.\n",
        )
        note = self.note(text, slug="2026-05-04-team-change")
        self.assert_pass(self.gate(note, self.redacted(name="2026-05-04-priya-departure.md")))

    def test_bare_day_log_slug_may_carry_a_derived_topic(self):
        text = NOTE.replace(f"sessions/{SLUG}.md", "sessions/2026-05-04.md").replace(
            f"sessions/redacted/{SLUG}.md", "sessions/redacted/2026-05-04.md"
        )
        self.assert_pass(self.gate(self.note(text), self.redacted(name="2026-05-04.md")))


class FilenameTaintTests(GateTestCase):
    """The slug, `source`, the notice path and an `Archived from` heading are verbatim
    copies of the sanctum filename that never pass through the body gate."""

    # A withheld block whose subject is lowercase vocabulary, with no proper noun in it.
    LEASE_BLOCK = (
        "the second office lease will not be renewed, and layoffs hit the platform group "
        "before the announcement."
    )

    def gate_slug(self, slug: str, body: str, note_text: str = NOTE) -> tuple[int, str, str]:
        red = self.redacted(withheld(body, source=f"{slug}.md"), name=f"{slug}.md")
        return self.gate(self.note(renamed(note_text, slug), slug=slug), red)

    def test_subject_in_the_slug_is_caught(self):
        cases = {
            "concatenated name": (
                "2026-05-04-personc-leaving", "Person C is leaving the company this quarter."
            ),
            "short name": ("2026-05-04-amy-departure", "Amy is departing next month."),
            "non-latin name": ("2026-05-04-\u5c71\u7530-dismissal", "\u5c71\u7530 was dismissed."),
            "lowercase topic words": (
                "2026-05-04-lease-layoffs", self.LEASE_BLOCK,
            ),
        }
        for label, (slug, body) in cases.items():
            with self.subTest(label):
                self.assert_fail(self.gate_slug(slug, body), "filename names a withheld subject")

    def test_lowercase_topic_words_in_provenance_and_notice_are_caught(self):
        # Only capitalised entities were compared against these copies, so a topic
        # spelled in lowercase in the withheld prose rode through all three.
        err = self.assert_fail(
            self.gate_slug("2026-05-04-lease-layoffs", self.LEASE_BLOCK),
            "provenance field names a withheld subject",
            "path in the body names a withheld subject",
        )
        self.assertNotIn("layoffs", err)

    def test_lowercase_topic_words_in_body_copies_are_caught(self):
        red = self.redacted(withheld(self.LEASE_BLOCK))
        cases = {
            "`Archived from` heading": NOTE + "\n## Archived from second office lease, layoffs\n",
            "path in the body": before_follow_up(
                NOTE, "Context lives in `sessions/office-lease-layoffs-platform.md`."
            ),
        }
        for label, text in cases.items():
            with self.subTest(label):
                self.assert_fail(
                    self.gate(self.note(text), red), f"{label} names a withheld subject"
                )

    def test_block_scalar_source_is_checked(self):
        # A line-prefix check never saw `source: >-` with the value on the following
        # line, so a subject rode through valid YAML.
        text = replace_once(
            NOTE,
            "redacted: true",
            "source: >-\n  sessions/2026-05-04-nightingale-close.md\nredacted: true",
        )
        red = self.redacted(withheld("Project Nightingale closes Friday."))
        self.assert_fail(
            self.gate(self.note(text), red), "provenance field names a withheld subject"
        )

    def test_common_word_does_not_fail_a_clean_slug(self):
        # The other direction, and just as important: a gate that fails ordinary
        # archives gets bypassed, which is a security outcome as much as a usability one.
        self.assert_pass(self.gate_slug("2026-05-04-team-update", "PersonD left the team."))


class NoticeTests(GateTestCase):
    def test_missing_notice_fails(self):
        # A silent hole reads as a complete record, which is worse than a visible gap.
        text = replace_once(NOTE, NOTICE, "")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "no reader-visible")

    def test_hidden_or_fenced_notice_does_not_satisfy_the_visible_rule(self):
        # An HTML comment or a code fence renders as nothing, so neither is a notice.
        for label, hidden in {
            "html comment": "<!-- Withheld from archive -->\n",
            "code fence": "```\n" + NOTICE + "```\n",
        }.items():
            with self.subTest(label):
                text = replace_once(NOTE, NOTICE, hidden)
                self.assert_fail(self.gate(self.note(text), self.redacted()), "no reader-visible")

    def test_missing_redacted_true_fails(self):
        text = replace_once(NOTE, "redacted: true\n", "")
        self.assert_fail(
            self.gate(self.note(text), self.redacted()), "lacks `redacted: true`"
        )

    def test_unknown_category_fails(self):
        text = replace_once(NOTE, "withheld: personnel.", "withheld: gossip.")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "outside the category")

    def test_category_naming_the_person_is_masked(self):
        text = replace_once(NOTE, "withheld: personnel.", "withheld: priya.")
        err = self.assert_fail(
            self.gate(self.note(text), self.redacted()), "names ['<withheld>']"
        )
        self.assert_no_withheld_names(err)

    def test_notice_naming_the_person_fails(self):
        text = replace_once(NOTE, "withheld: personnel.", "withheld: Priya's departure.")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "closed vocabulary")

    def test_redacted_count_disagreeing_with_notices_fails(self):
        text = replace_once(NOTE, "redacted_count: 1", "redacted_count: 2")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "redacted_count: 2")

    def test_missing_redacted_count_fails(self):
        text = replace_once(NOTE, "redacted_count: 1\n", "")
        self.assert_fail(self.gate(self.note(text), self.redacted()), "positive `redacted_count`")

    def test_notice_prints_path_although_source_withheld_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", "source_withheld: true")
        note = self.note(text, slug="2026-05-04-team-change")
        self.assert_fail(
            self.gate(note, self.redacted()), "prints a path although `source_withheld: true`"
        )

    def test_notice_pointing_at_another_redacted_file_fails(self):
        text = replace_once(
            NOTE, f"sessions/redacted/{SLUG}.md", "sessions/redacted/2026-05-04-other-log.md"
        )
        self.assert_fail(self.gate(self.note(text), self.redacted()), "points at")

    def test_notice_path_naming_the_subject_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", "source_withheld: true")
        text = replace_once(
            text, f"`sessions/redacted/{SLUG}.md`", "`sessions/redacted/priya-exit.md`"
        )
        self.assert_fail(
            self.gate(self.note(text, slug="2026-05-04-team-change"), self.redacted()),
            "path in the body names a withheld subject",
        )


class CleanClaimTests(GateTestCase):
    def test_redacted_true_without_a_redacted_file_fails(self):
        text = replace_once(CLEAN_NOTE, "redacted: false", "redacted: true")
        self.assert_fail(self.gate(self.note(text)), "`redacted: true` but no redacted file")

    def test_nonzero_count_without_a_redacted_file_fails(self):
        text = replace_once(CLEAN_NOTE, "redacted: false\n", "redacted: false\nredacted_count: 1\n")
        self.assert_fail(self.gate(self.note(text)), "nonzero `redacted_count`")

    def test_notice_without_a_redacted_file_fails(self):
        text = replace_once(CLEAN_NOTE, FOLLOW_UP, NOTICE + "\n" + FOLLOW_UP)
        self.assert_fail(self.gate(self.note(text)), "the body carries a withheld notice")

    def test_missing_redacted_false_fails(self):
        text = replace_once(CLEAN_NOTE, "redacted: false\n", "")
        self.assert_fail(self.gate(self.note(text)), "must state `redacted: false`")


if __name__ == "__main__":
    unittest.main()
