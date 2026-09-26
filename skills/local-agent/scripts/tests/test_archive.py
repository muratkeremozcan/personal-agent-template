#!/usr/bin/env python3
"""Tests for the archive tier: where aged logs go, and the gate on what may follow them.

The behaviours worth pinning here are the ones where a wrong answer looks like a right
one. An aged log reported with no destination reads as an instruction to delete. A
redaction verifier that passes a leak is worse than no verifier, because it converts a
manual check into false confidence.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import _sanctum  # noqa: E402

VERIFIER = SCRIPTS / "verify_archive_redaction.py"


class ArchiveRootTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_absent_when_unset_and_default_missing(self):
        # Unset is a decision and returns None. An explicit bad path is a typo and raises;
        # see test_misconfigured_path_raises_rather_than_reading_as_absent.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("LOCAL_AGENT_ARCHIVE", None)
            os.environ["LOCAL_AGENT_HOME"] = tmp
            self.assertIsNone(_sanctum.archive_root())

    def test_misconfigured_path_raises_rather_than_reading_as_absent(self):
        # "The owner chose not to have an archive" and "the variable has a typo" permit
        # different things. Collapsing them lets a typo silently license deletion.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCAL_AGENT_ARCHIVE"] = str(Path(tmp) / "nope")
            with self.assertRaises(_sanctum.ArchiveMisconfigured):
                _sanctum.archive_root()

    def test_found_when_directory_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCAL_AGENT_ARCHIVE"] = tmp
            self.assertEqual(_sanctum.archive_root(), Path(tmp).resolve())

    def test_target_derives_year_and_month_from_filename(self):
        root = Path("/a/archive")
        self.assertEqual(
            _sanctum.archive_target("2026-05-04-topic.md", root),
            str(root / "log" / "2026" / "05" / "2026-05-04-topic.md"),
        )

    def test_target_is_none_without_a_date(self):
        # Guessed at rather than reported is how a log lands in the wrong month
        # and stops being findable by the only index anyone uses, which is the date.
        self.assertIsNone(_sanctum.archive_target("no-date.md", Path("/a")))

    def test_archive_inside_the_sanctum_is_rejected(self):
        # An archive inside the sanctum gets loaded on waking, counted against the token
        # budget, and curated like identity, which is the opposite of an archive.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCAL_AGENT_HOME"] = tmp
            sanctum = _sanctum.sanctum_path()
            (sanctum / "sessions").mkdir(parents=True)
            os.environ["LOCAL_AGENT_ARCHIVE"] = str(sanctum / "sessions")
            with self.assertRaises(_sanctum.ArchiveMisconfigured):
                _sanctum.archive_root()

    def test_target_rejects_a_date_in_a_directory_component(self):
        # stale_logs globs non-recursively and reads path.name, so a date in a parent
        # directory must not produce a destination the staleness rule never saw.
        self.assertIsNone(
            _sanctum.archive_target("sessions/2020-07-08/no-date.md", Path("/a")))

    def test_target_rejects_an_impossible_date(self):
        # stale_logs rejects month 99; the destination derivation has to agree.
        self.assertIsNone(_sanctum.archive_target("2026-99-99-topic.md", Path("/a")))

    def test_undated_logs_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            sanctum = Path(tmp)
            (sanctum / "sessions").mkdir()
            (sanctum / "sessions" / "no-date.md").write_text("x", encoding="utf-8")
            (sanctum / "sessions" / "2026-05-04-topic.md").write_text("x", encoding="utf-8")
            self.assertEqual(_sanctum.undated_logs(sanctum), ["no-date.md"])

    def test_target_month_matches_the_staleness_rule(self):
        # Both read the date from the filename. If these ever diverge, a log can be
        # called stale for one month and archived into another.
        name = "2026-01-31-topic.md"
        with tempfile.TemporaryDirectory() as tmp:
            sanctum = Path(tmp)
            (sanctum / "sessions").mkdir()
            (sanctum / "sessions" / name).write_text("x", encoding="utf-8")
            (sanctum / _sanctum.BORN_MARKER).write_text("2020-01-01", encoding="utf-8")
            stale = _sanctum.stale_logs(sanctum, 14, date(2026, 6, 1))
        self.assertIn(name, stale)
        self.assertIn("/2026/01/", _sanctum.archive_target(name, Path("/a")))


class RedactionGateTest(unittest.TestCase):
    """Every case here is one the gate must refuse. A passing leak is the failure mode.

    Each run builds the layout the archive procedure writes: the note under
    <archive>/log/YYYY/MM/ and the withheld blocks in the sanctum's sessions/redacted/
    under the source filename, so the structural checks pass and the case under test is
    the only thing that can fail.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.archive = root / "archive"
        self.archive.mkdir()
        home = root / "home"
        self.redacted_dir = home / "_bmad" / "memory" / "local-agent" / "sessions" / "redacted"
        self.redacted_dir.mkdir(parents=True)
        self.env = {
            **os.environ, "LOCAL_AGENT_HOME": str(home), "LOCAL_AGENT_ARCHIVE": str(self.archive),
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _note(self, text: str, name: str = "2026-05-04-topic.md") -> Path:
        path = self.archive / "log" / "2026" / "05" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _gate(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(VERIFIER), *map(str, args)],
            capture_output=True, text=True, check=False, env=self.env,
        )

    def _run(self, archived: str, withheld: str, name="2026-05-04-topic.md", expect=None,
             extra=()):
        # The slug is the sanctum filename, so `source:` follows the name under test.
        a = self._note(archived.replace("2026-05-04-topic", Path(name).stem), name)
        w = self.redacted_dir / name
        w.write_text(withheld, encoding="utf-8")
        result = self._gate(a, w, "--quiet", *extra)
        if expect is not None:
            self.assertIn(expect, result.stderr)
        return result.returncode

    WITHHELD = (
        "## Withheld from 2026-05-04-topic.md\n\n"
        "**Confidential:** Person A told Person B that Person C is leaving the company "
        "at the end of the quarter.\n"
    )
    NOTICE = "> [!warning] Withheld from archive\n> 1 block withheld: personnel.\n"
    CLEAN = (
        "---\ntype: session-log\ndate: 2026-05-04\nsource: sessions/2026-05-04-topic.md\n"
        "redacted: true\nredacted_count: 1\n---\n"
        "# Topic\n\nOrdinary content.\n\n" + NOTICE
    )

    def test_clean_note_passes(self):
        self.assertEqual(self._run(self.CLEAN, self.WITHHELD), 0)

    def test_verbatim_leak_is_caught(self):
        leaked = self.CLEAN + (
            "\nPerson A told Person B that Person C is leaving the company at the end of "
            "the quarter.\n"
        )
        self.assertEqual(
            self._run(leaked, self.WITHHELD, expect="withheld sentence present"), 1
        )

    def test_missing_notice_is_caught(self):
        # A silent hole reads as a complete record, which is worse than a visible gap.
        silent = self.CLEAN.replace(self.NOTICE, "")
        self.assertEqual(self._run(silent, self.WITHHELD, expect="no reader-visible"), 1)

    def test_tainted_filename_is_caught(self):
        # The filename never passes through the body gate, so a slug naming the subject
        # leaks it even when every block was withheld.
        self.assertEqual(
            self._run(self.CLEAN, self.WITHHELD, name="2026-05-04-personc-leaving.md",
                      expect="filename names a withheld subject"),
            1,
        )

    def test_unreadable_input_fails_closed(self):
        w = self.redacted_dir / "2026-05-04-topic.md"
        w.write_text(self.WITHHELD, encoding="utf-8")
        rc = self._gate(self.archive / "missing.md", w).returncode
        # 2 rather than 1: a check that could not run must block the prune exactly
        # as a failing one does, and must be distinguishable from a clean pass.
        self.assertEqual(rc, 2)

    def test_short_withheld_sentence_is_caught(self):
        # Regression: a withheld sentence under the length floor once passed both the
        # sentence check and the word-run check, so a termination and a severance figure
        # archived verbatim. The token sweep is what closes this.
        withheld = ("## Withheld from 2026-05-04-topic.md\n\n"
                    "**Confidential:** PersonC was fired. Severance: 185000.\n")
        leaked = self.CLEAN.replace("Ordinary content.",
                                    "Ordinary content. PersonC was fired. Severance: 185000.")
        self.assertEqual(self._run(leaked, withheld, expect="tokens from the withheld"), 1)

    def test_entity_named_only_in_withheld_block_is_caught(self):
        # Regression, and the leak this capability's own documentation calls the subtlest
        # it can produce: the body shows nothing while the frontmatter announces who the
        # withheld block was about. Only `source:` used to be checked.
        withheld = ("## Withheld from 2026-05-04-topic.md\n\n"
                    "**Confidential:** PersonD is being managed out after a review.\n")
        note = self.CLEAN.replace("redacted: true",
                                  'people: ["[[person/PersonD]]"]\nredacted: true')
        self.assertEqual(self._run(note, withheld, expect="persond"), 1)

    def test_allow_permits_a_genuine_collision(self):
        # The sweep is deliberately biased toward flagging, so an escape hatch has to exist
        # or a legitimate shared word makes an archive permanently unrunnable.
        withheld = ("## Withheld from 2026-05-04-topic.md\n\n"
                    "**Confidential:** PersonC was fired.\n")
        leaked = self.CLEAN.replace("Ordinary content.", "Ordinary content. PersonC was fired.")
        self.assertEqual(self._run(leaked, withheld), 1)
        rc = self._run(leaked, withheld, extra=("--allow", "personc", "--allow", "fired"))
        self.assertEqual(rc, 0)

    def test_credential_shaped_value_is_caught_regardless_of_length(self):
        # Regression: `PIN: 1234` is neither long prose nor seven words, so both
        # thresholds missed it. Length was never the right axis for a secret.
        withheld = ("## Withheld from 2026-05-04-topic.md\n\n"
                    "**Confidential:** Access PIN: 1234.\n")
        leaked = self.CLEAN.replace("Ordinary content.", "Ordinary content. PIN: 1234")
        self.assertEqual(
            self._run(leaked, withheld, expect="credential-shaped value from the withheld"), 1
        )

    def test_short_name_in_slug_is_caught(self):
        # Regression: a four-character floor dropped short given names, which are
        # exactly what a personnel redaction is about.
        withheld = "## Withheld from x.md\n\n**Confidential:** Amy is departing next month.\n"
        self.assertEqual(
            self._run(self.CLEAN, withheld, name="2026-05-04-amy-departure.md",
                      expect="filename names a withheld subject"),
            1,
        )

    def test_concatenated_slug_is_caught(self):
        # Regression: `personc-leaving` tokenises to "personc", matching neither
        # "person" nor "c", so a subject travelled through by concatenation.
        withheld = "## Withheld from x.md\n\n**Confidential:** Person C is leaving.\n"
        self.assertEqual(
            self._run(self.CLEAN, withheld, name="2026-05-04-personc-leaving.md",
                      expect="filename names a withheld subject"),
            1,
        )

    def test_non_latin_slug_is_caught(self):
        # Regression: an ASCII tokeniser saw nothing in a CJK filename. Scripts with
        # no case system always qualify as entities, because case cannot rule them out.
        withheld = "## Withheld from x.md\n\n**Confidential:** \u5c71\u7530 was dismissed.\n"
        self.assertEqual(
            self._run(self.CLEAN, withheld, name="2026-05-04-\u5c71\u7530-dismissal.md",
                      expect="filename names a withheld subject"),
            1,
        )

    def test_common_word_does_not_fail_a_clean_slug(self):
        # The other direction, and just as important: a gate that fails ordinary
        # archives gets bypassed, which is a security outcome as much as a usability one.
        withheld = "## Withheld from x.md\n\n**Confidential:** PersonD left the team.\n"
        self.assertEqual(self._run(self.CLEAN, withheld, name="2026-05-04-team-update.md"), 0)

    def test_block_scalar_source_is_checked(self):
        # Regression: a line-prefix check never saw `source: >-` with the value on the
        # following line, so a subject rode through valid YAML.
        withheld = ("## Withheld from x.md\n\n"
                    "**Confidential:** Project Nightingale closes Friday.\n")
        note = self.CLEAN.replace(
            "redacted: true",
            "source: >-\n  sessions/2026-05-04-nightingale-close.md\nredacted: true")
        self.assertEqual(
            self._run(note, withheld, expect="provenance field names a withheld subject"), 1
        )

    def test_hidden_notice_does_not_satisfy_the_visible_rule(self):
        # Regression: an HTML comment renders as nothing, so it is not a notice.
        hidden = self.CLEAN.replace(self.NOTICE, "<!-- Withheld from archive -->\n")
        self.assertEqual(self._run(hidden, self.WITHHELD, expect="no reader-visible"), 1)

    def test_fenced_notice_does_not_satisfy_the_visible_rule(self):
        fenced = self.CLEAN.replace(self.NOTICE, "```\n" + self.NOTICE + "```\n")
        self.assertEqual(self._run(fenced, self.WITHHELD, expect="no reader-visible"), 1)

    CLEAN_LOG = (
        "---\ntype: session-log\ndate: 2026-05-04\nsource: sessions/2026-05-04-topic.md\n"
        "redacted: false\n---\n# Topic\n\nNothing withheld here.\n"
    )

    def test_clean_log_verifies_without_a_redacted_file(self):
        # A log with nothing withheld had no redacted file, so the mandatory step could
        # not run and was skipped in practice. A skipped step is the escape a missed
        # redaction needs, so the clean case asserts its claim explicitly.
        rc = self._gate(self._note(self.CLEAN_LOG), "--no-withheld", "--quiet").returncode
        self.assertEqual(rc, 0)

    def test_clean_claim_contradicted_by_frontmatter_fails(self):
        note = self.CLEAN_LOG.replace("redacted: false", "redacted: true")
        result = self._gate(self._note(note), "--no-withheld", "--quiet")
        self.assertEqual(result.returncode, 1)
        self.assertIn("`redacted: true` but no redacted file", result.stderr)

    def test_empty_withheld_file_fails_closed(self):
        # An empty redacted file means the withheld material was lost. Passing here
        # would let the source log be pruned, destroying the only remaining copy.
        self.assertEqual(self._run(self.CLEAN, ""), 2)


if __name__ == "__main__":
    unittest.main()
