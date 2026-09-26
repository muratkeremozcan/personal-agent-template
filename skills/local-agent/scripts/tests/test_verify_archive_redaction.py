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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "verify_archive_redaction.py"
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

# The same log with nothing withheld: no marked paragraph, no notice, `redacted: false`.
CLEAN_SOURCE = SOURCE.replace(REDACTED.split("\n", 2)[2], "")
CLEAN_NOTE = NOTE.replace("redacted: true\nredacted_count: 1\n", "redacted: false\n").replace(
    NOTICE + "\n", ""
)


def replace_once(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"expected exactly one occurrence of {old!r}"
    return text.replace(old, new)


def before_follow_up(text: str, addition: str) -> str:
    return replace_once(text, FOLLOW_UP, addition + "\n\n" + FOLLOW_UP)


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
        """Call main() in-process. Returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = gate.main([str(a) for a in args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

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

    def assert_cannot_run(self, result: tuple[int, str, str], fragment: str) -> None:
        code, _, err = result
        self.assertEqual(code, 2, err)
        self.assertIn(fragment, err)


class ContractTests(GateTestCase):
    def test_clean_archive_with_source_passes(self):
        code, out, err = self.run_gate(self.note(), self.redacted(), "--source", self.source())
        self.assertEqual(code, 0, err)
        self.assertIn("OK", out)
        self.assertIn("survives", out)

    def test_nothing_withheld_passes_with_no_withheld(self):
        code, out, err = self.run_gate(
            self.note(CLEAN_NOTE), "--no-withheld", "--source", self.source(CLEAN_SOURCE)
        )
        self.assertEqual(code, 0, err)
        self.assertIn("nothing withheld", out)

    def test_quiet_prints_nothing_on_success(self):
        code, out, _ = self.run_gate(self.note(), self.redacted(), "--quiet")
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_cli_exit_codes(self):
        env = {**os.environ, **self.env}
        note, red = self.note(), self.redacted()
        ok = subprocess.run(
            [sys.executable, str(SCRIPT), str(note), str(red)],
            capture_output=True, text=True, check=False, env=env,
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        missing = subprocess.run(
            [sys.executable, str(SCRIPT), str(note), str(red.with_name("nope.md"))],
            capture_output=True, text=True, check=False, env=env,
        )
        self.assertEqual(missing.returncode, 2, missing.stderr)


class FailClosedTests(GateTestCase):
    def test_unexpected_exception_exits_2(self):
        with mock.patch.object(gate, "main", side_effect=RuntimeError("boom")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(gate.entrypoint([]), 2)
        self.assertIn("cannot run", err.getvalue())

    def test_empty_note_exits_2(self):
        self.assert_cannot_run(self.run_gate(self.note("\n\n"), self.redacted()), "is empty")

    def test_empty_source_exits_2(self):
        self.assert_cannot_run(
            self.run_gate(self.note(), self.redacted(), "--source", self.source("\n")),
            "is empty",
        )

    def test_missing_source_exits_2(self):
        self.assert_cannot_run(
            self.run_gate(self.note(), self.redacted(), "--source", self.sessions / "nope.md"),
            "source log",
        )

    def test_redacted_file_outside_sessions_redacted_exits_2(self):
        stray = self.root / "elsewhere" / f"{SLUG}.md"
        stray.parent.mkdir()
        stray.write_text(REDACTED, encoding="utf-8")
        self.assert_cannot_run(self.run_gate(self.note(), stray), "not under sessions/redacted/")

    def test_shingle_out_of_range_exits_2(self):
        note, red = self.note(), self.redacted()
        self.assert_cannot_run(self.run_gate(note, red, "--shingle", "2"), "--shingle")
        self.assert_cannot_run(self.run_gate(note, red, "--shingle", "13"), "--shingle")

    def test_misconfigured_archive_root_exits_2(self):
        note, red = self.note(), self.redacted()
        with mock.patch.dict(os.environ, {"LOCAL_AGENT_ARCHIVE": str(self.root / "typo")}):
            self.assert_cannot_run(self.run_gate(note, red), "LOCAL_AGENT_ARCHIVE")

    def test_failure_preview_does_not_reprint_the_whole_sentence(self):
        err = self.assert_fail(
            self.run_gate(self.note(before_follow_up(NOTE, WITHHELD_SENTENCE)), self.redacted()),
            "withheld sentence present",
        )
        tail = " ".join(gate.words(WITHHELD_SENTENCE.split(". ", 1)[1]))
        self.assertGreater(len(tail), gate.PREVIEW_CHARS)
        self.assertNotIn(tail, err)


class SourceCompletenessTests(GateTestCase):
    def test_sentence_lost_from_both_files_fails(self):
        # Partial redaction: the source carries a second marked sentence that reached
        # neither the note nor the redacted file, so the prune would destroy it.
        source = before_follow_up(
            SOURCE, "Omar is on a performance plan until July and nobody else knows."
        )
        self.assert_fail(
            self.run_gate(self.note(), self.redacted(), "--source", self.source(source)),
            "in neither the archived note nor the redacted file",
        )

    def test_sentence_lost_without_withheld_material_fails(self):
        source = before_follow_up(CLEAN_SOURCE, "Budget for the pilot is still unresolved.")
        self.assert_fail(
            self.run_gate(self.note(CLEAN_NOTE), "--no-withheld", "--source", self.source(source)),
            "in neither the archived note nor the redacted file",
        )

    def test_redacted_text_not_verbatim_from_source_fails(self):
        redacted = replace_once(REDACTED, "Priya is leaving", "Priya is departing")
        self.assert_fail(
            self.run_gate(self.note(), self.redacted(redacted), "--source", self.source()),
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
            self.run_gate(self.note(), self.redacted(redacted), "--source", self.source(source))
        )

    def test_source_not_listed_in_frontmatter_fails(self):
        s = self.source(name="2026-05-04-other-log.md")
        self.assert_fail(
            self.run_gate(self.note(), self.redacted(), "--source", s),
            "is not listed under `source`",
        )


class MarkerTests(GateTestCase):
    def test_marker_inside_the_notice_callout_fails(self):
        marked = replace_once(
            NOTE, NOTICE_BODY, NOTICE_BODY + "> **Confidential:** the Acme lease ends in June.\n"
        )
        self.assert_fail(
            self.run_gate(self.note(marked), self.redacted()), "confidentiality marker"
        )

    def test_confidential_label_in_the_note_fails(self):
        # The marked block never reached the redacted file, so no text comparison can
        # see it. The marker itself is the evidence.
        text = before_follow_up(NOTE, "**Confidential:** the Acme office lease ends in June.")
        self.assert_fail(
            self.run_gate(self.note(text), self.redacted()), "confidentiality marker"
        )

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
                    self.run_gate(self.note(before_follow_up(NOTE, line)), self.redacted()),
                    "confidentiality marker",
                )

    def test_marker_fails_even_when_nothing_was_declared_withheld(self):
        text = before_follow_up(CLEAN_NOTE, "**Confidential:** the lease ends in June.")
        self.assert_fail(
            self.run_gate(self.note(text), "--no-withheld"), "confidentiality marker"
        )

    def test_bare_private_or_sensitive_mid_sentence_is_not_a_marker(self):
        text = before_follow_up(
            NOTE, "Lena moved the private repo and the sensitive-data scanner to the new org."
        )
        self.assert_pass(self.run_gate(self.note(text), self.redacted()))


# Credential-shaped test values are assembled at runtime so no literal in this file
# matches a real token format; secret scanners on the hosting side reject pushes that
# carry one, fake or not.
def fake(prefix: str, body: str) -> str:
    return prefix + body


class CredentialShapeTests(GateTestCase):
    GHP = fake("gh" + "p_", "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")

    def test_github_token_in_note_fails_without_echoing_it(self):
        text = before_follow_up(NOTE, f"The CI token is {self.GHP} for now.")
        err = self.assert_fail(
            self.run_gate(self.note(text), self.redacted()), "credential-shaped value (github"
        )
        self.assertNotIn(self.GHP, err)
        self.assertNotIn(self.GHP[4:20], err)

    def test_every_named_shape_fails_without_echoing_it(self):
        cases = {
            "slack": fake("xo" + "xb-", "1234567890-abcdefghijklmnop"),
            "github fine-grained": fake("github" + "_pat_", "11ABCDEFG0123456789_abcdefghijklmnop"),
            "gitlab": fake("gl" + "pat-", "abcdefghij0123456789"),
            "openai": fake("s" + "k-", "abcdefghijklmnopqrstuvwxyz0123"),
            "anthropic": fake("s" + "k-ant-api03-", "abcdefghijklmnopqrstuvwxyz"),
            "aws": fake("AK" + "IA", "IOSFODNN7EXAMPLE"),
            "google": fake("AI" + "za", "SyA-abcdefghijklmnopqrstuvwxyz01234"),
            "jwt": fake(
                "ey" + "JhbGciOiJIUzI1NiJ9.",
                "eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            ),
            "pem": fake("-----BEGIN RSA ", "PRIVATE KEY-----"),
            "bearer": "Bearer abcdefghijklmnopqrstuvwxyz0123",
            "cookie": "cookie: session=abcdefghijklmnopqrstuvwx",
            "token path": "~/.config/acme/application_default_credentials.json",
        }
        for label, value in cases.items():
            with self.subTest(label):
                text = before_follow_up(NOTE, f"Recorded here: {value}")
                err = self.assert_fail(
                    self.run_gate(self.note(text), self.redacted()), "credential-shaped value"
                )
                self.assertNotIn(value, err)

    def test_shape_in_frontmatter_fails(self):
        text = replace_once(NOTE, "redacted: true\n", f"token: {self.GHP}\nredacted: true\n")
        err = self.assert_fail(self.run_gate(self.note(text), self.redacted()), "on line 8")
        self.assertNotIn(self.GHP, err)

    def test_shape_fails_in_a_clean_archive_too(self):
        text = before_follow_up(CLEAN_NOTE, f"Token {self.GHP}.")
        self.assert_fail(
            self.run_gate(self.note(text), "--no-withheld"), "credential-shaped value"
        )

    def test_mentioning_a_token_without_its_value_passes(self):
        text = before_follow_up(NOTE, "Lena rotated the CI token and the deploy key on Friday.")
        self.assert_pass(self.run_gate(self.note(text), self.redacted()))

    def test_withheld_credential_value_is_not_echoed(self):
        # The template once printed the matched value in the failure line, which made the
        # report a second copy of the secret.
        redacted = REDACTED + "\nAccess PIN: 482913.\n"
        text = before_follow_up(NOTE, "PIN: 482913")
        err = self.assert_fail(
            self.run_gate(self.note(text), self.redacted(redacted)),
            "credential-shaped value from the withheld text",
            "value not printed",
        )
        self.assertNotIn("482913", err)

    def test_leaked_digit_token_is_described_by_length(self):
        redacted = REDACTED + "\nSeverance agreed at 185000 for Priya.\n"
        text = before_follow_up(NOTE, "Budget line 185000 is closed.")
        err = self.assert_fail(self.run_gate(self.note(text), self.redacted(redacted)))
        self.assertNotIn("185000", err)
        self.assertIn("6-char value", err)


class NormalizationTests(GateTestCase):
    def test_homoglyph_and_zero_width_sentence_is_caught(self):
        # One Cyrillic vowel and one zero-width space in the note copy defeated both the
        # exact sentence match and every word run before normalisation folded them away.
        redacted = REDACTED + "\nPriya told Omar she is leaving in June.\n"
        disguised = "Priya told Om\u0430r she is lea\u200bving in June."
        self.assert_fail(
            self.run_gate(self.note(before_follow_up(NOTE, disguised)), self.redacted(redacted)),
            "withheld sentence present",
        )

    def test_homoglyph_and_zero_width_word_is_caught(self):
        disguised = "Lena thanked Pr\u200bi\u0443a for the review."
        err = self.assert_fail(
            self.run_gate(self.note(before_follow_up(NOTE, disguised)), self.redacted()),
            "tokens from the withheld text",
        )
        self.assertIn("'priya'", err)

    def test_diacritics_do_not_hide_a_name(self):
        redacted = REDACTED + "\nJos\u00e9 is being moved off the team.\n"
        text = before_follow_up(NOTE, "Jose reviewed the plan.")
        self.assert_fail(
            self.run_gate(self.note(text), self.redacted(redacted)),
            "tokens from the withheld text",
            "jose",
        )

    def test_cyrillic_withheld_sentence_is_caught(self):
        # An ASCII word pattern sees nothing in Cyrillic, so this passed every check.
        sentence = (
            "\u041e\u043b\u044c\u0433\u0430 \u0443\u0445\u043e\u0434\u0438\u0442 "
            "\u0438\u0437 \u043a\u043e\u043c\u043f\u0430\u043d\u0438\u0438 \u0432 "
            "\u043a\u043e\u043d\u0446\u0435 \u043c\u0435\u0441\u044f\u0446\u0430."
        )
        redacted = REDACTED + "\n" + sentence + "\n"
        self.assert_fail(
            self.run_gate(self.note(before_follow_up(NOTE, sentence)), self.redacted(redacted)),
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
        self.assert_pass(
            self.run_gate(self.note(text), self.redacted(), "--source", self.source(source))
        )

    def test_bom_and_crlf_do_not_hide_a_tainted_source_line(self):
        # A byte-order mark once made the frontmatter parse miss, and the source line
        # check was skipped without a word.
        text = replace_once(
            NOTE, f"source: sessions/{SLUG}.md", "source: sessions/2026-05-04-priya-leaving.md"
        )
        text = "\ufeff" + text.replace("\n", "\r\n")
        self.assert_fail(
            self.run_gate(self.note(text), self.redacted(name="2026-05-04-priya-leaving.md")),
            "provenance field names a withheld subject",
            "priya",
        )

    def test_bom_and_crlf_on_a_clean_archive_pass(self):
        note = self.note("\ufeff" + NOTE.replace("\n", "\r\n"))
        source = self.source("\ufeff" + SOURCE.replace("\n", "\r\n"))
        self.assert_pass(self.run_gate(note, self.redacted(), "--source", source))


class SentenceVariantTests(GateTestCase):
    def test_stripped_label_and_punctuation_do_not_hide_a_short_leak(self):
        # Six words after the label sit below the shingle size. The whole-sentence match
        # was defeated by dropping the label, the comma, the period and by a curly
        # apostrophe.
        redacted = REDACTED + "\n**Confidential:** Priya's resignation, effective June thirtieth.\n"
        text = before_follow_up(NOTE, "Priya\u2019s resignation effective June thirtieth")
        self.assert_fail(
            self.run_gate(self.note(text), self.redacted(redacted)), "withheld sentence present"
        )

    def test_sentence_split_across_lines_and_emphasis_is_caught(self):
        text = before_follow_up(
            NOTE, "Omar plans to *reach out*\npersonally afterwards, noted here only for"
        )
        self.assert_fail(
            self.run_gate(self.note(text), self.redacted()), "withheld 7-word run present"
        )

    def test_short_common_phrase_shared_with_withheld_text_passes(self):
        text = before_follow_up(NOTE, "Lena asked for the draft by Friday.")
        self.assert_pass(self.run_gate(self.note(text), self.redacted()))

    def test_date_in_withheld_prose_does_not_fail_on_the_year(self):
        # The note's own date is public by construction, so a withheld sentence naming a
        # day of the same year must not fail every archive on "2026" alone.
        redacted = REDACTED + "\nPriya's last day is 2026-05-30.\n"
        self.assert_pass(self.run_gate(self.note(), self.redacted(redacted)))


class StructureTests(GateTestCase):
    def test_missing_or_unterminated_frontmatter_fails(self):
        for text in (NOTE.split("---\n", 2)[2], NOTE.replace("---\n# Pipeline", "# Pipeline")):
            with self.subTest(text[:12]):
                self.assert_fail(
                    self.run_gate(self.note(text), self.redacted()), "no closed frontmatter"
                )

    def test_missing_date_fails(self):
        text = replace_once(NOTE, "date: 2026-05-04\n", "")
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "no valid `date")

    def test_wrong_month_directory_fails(self):
        self.assert_fail(
            self.run_gate(self.note(month="2026/06"), self.redacted()), "not under log/2026/05/"
        )

    def test_note_outside_the_configured_archive_fails(self):
        other = self.root / "elsewhere" / "log" / "2026" / "05" / f"{SLUG}.md"
        other.parent.mkdir(parents=True)
        other.write_text(NOTE, encoding="utf-8")
        self.assert_fail(self.run_gate(other, self.redacted()), "not inside the configured archive")

    def test_slug_date_disagreeing_with_frontmatter_fails(self):
        self.assert_fail(
            self.run_gate(self.note(slug="2026-05-05-pipeline-rollout"), self.redacted()),
            "does not start with date 2026-05-04",
        )

    def test_source_and_source_withheld_together_fails(self):
        text = replace_once(NOTE, "redacted: true\n", "source_withheld: true\nredacted: true\n")
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "carries both `source`")

    def test_neither_source_nor_source_withheld_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md\n", "")
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "carries neither")

    def test_slug_not_matching_any_source_fails(self):
        text = NOTE.replace(SLUG, "2026-05-04-other-log")
        r = self.redacted(name="2026-05-04-other-log.md")
        self.assert_fail(self.run_gate(self.note(text), r), "matches none of its source filenames")

    def test_redacted_file_named_after_a_different_source_fails(self):
        r = self.redacted(name="2026-05-04-other-log.md")
        self.assert_fail(self.run_gate(self.note(), r), "is not named after any `source`")

    def test_reslugged_archive_with_source_withheld_passes(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", "source_withheld: true")
        text = replace_once(
            text,
            NOTICE_BODY,
            "> 1 block withheld: personnel. Full text stays in the sanctum.\n"
            "> Source date: 2026-05-04.\n",
        )
        note = self.note(text, slug="2026-05-04-team-change")
        self.assert_pass(self.run_gate(note, self.redacted(name="2026-05-04-priya-departure.md")))

    def test_bare_day_log_slug_may_carry_a_derived_topic(self):
        text = NOTE.replace(f"sessions/{SLUG}.md", "sessions/2026-05-04.md").replace(
            f"sessions/redacted/{SLUG}.md", "sessions/redacted/2026-05-04.md"
        )
        self.assert_pass(self.run_gate(self.note(text), self.redacted(name="2026-05-04.md")))


class NoticeTests(GateTestCase):
    def test_unknown_category_fails(self):
        text = replace_once(NOTE, "withheld: personnel.", "withheld: gossip.")
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "outside the category")

    def test_notice_naming_the_person_fails(self):
        text = replace_once(NOTE, "withheld: personnel.", "withheld: Priya's departure.")
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "closed vocabulary")

    def test_redacted_count_disagreeing_with_notices_fails(self):
        text = replace_once(NOTE, "redacted_count: 1", "redacted_count: 2")
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "redacted_count: 2")

    def test_missing_redacted_count_fails(self):
        text = replace_once(NOTE, "redacted_count: 1\n", "")
        self.assert_fail(
            self.run_gate(self.note(text), self.redacted()), "positive `redacted_count`"
        )

    def test_notice_prints_path_although_source_withheld_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", "source_withheld: true")
        note = self.note(text, slug="2026-05-04-team-change")
        self.assert_fail(
            self.run_gate(note, self.redacted()), "prints a path although `source_withheld: true`"
        )

    def test_notice_pointing_at_another_redacted_file_fails(self):
        text = replace_once(
            NOTE, f"sessions/redacted/{SLUG}.md", "sessions/redacted/2026-05-04-other-log.md"
        )
        self.assert_fail(self.run_gate(self.note(text), self.redacted()), "points at")

    def test_notice_path_naming_the_subject_fails(self):
        text = replace_once(NOTE, f"source: sessions/{SLUG}.md", "source_withheld: true")
        text = replace_once(
            text, f"`sessions/redacted/{SLUG}.md`", "`sessions/redacted/priya-exit.md`"
        )
        self.assert_fail(
            self.run_gate(self.note(text, slug="2026-05-04-team-change"), self.redacted()),
            "path in the body names a withheld subject",
        )


if __name__ == "__main__":
    unittest.main()
