#!/usr/bin/env python3
"""Tests for the archive tier: where aged logs go, and the gate on what may follow them.

The behaviours worth pinning here are the ones where a wrong answer looks like a right
one. An aged log reported with no destination reads as an instruction to delete. A
redaction verifier that passes a leak is worse than no verifier, because it converts a
manual check into false confidence.
"""

import json
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

    def test_target_month_matches_the_staleness_rule(self):
        # Both read the date from the filename. If these ever diverge, a log can be
        # called stale for one month and archived into another.
        name = "2026-01-31-topic.md"
        with tempfile.TemporaryDirectory() as tmp:
            sanctum = Path(tmp)
            (sanctum / "sessions").mkdir()
            (sanctum / "sessions" / name).write_text("x")
            (sanctum / _sanctum.BORN_MARKER).write_text("2020-01-01")
            stale = _sanctum.stale_logs(sanctum, 14, date(2026, 6, 1))
        self.assertIn(name, stale)
        self.assertIn("/2026/01/", _sanctum.archive_target(name, Path("/a")))


class RedactionGateTest(unittest.TestCase):
    """Every case here is one the gate must refuse. A passing leak is the failure mode."""

    def _run(self, archived: str, withheld: str, name="2026-05-04-topic.md"):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / name
            w = Path(tmp) / "withheld.md"
            a.write_text(archived)
            w.write_text(withheld)
            return subprocess.run(
                [sys.executable, str(VERIFIER), str(a), str(w), "--quiet"],
                capture_output=True, text=True,
            ).returncode

    WITHHELD = (
        "## Withheld from 2026-05-04-topic.md\n\n"
        "**Confidential:** Person A told Person B that Person C is leaving the company "
        "at the end of the quarter.\n"
    )
    CLEAN = (
        "---\ntype: session-log\ndate: 2026-05-04\nredacted: true\n---\n"
        "# Topic\n\nOrdinary content.\n\n"
        "> [!warning] Withheld from archive\n> 1 block withheld: personnel.\n"
    )

    def test_clean_note_passes(self):
        self.assertEqual(self._run(self.CLEAN, self.WITHHELD), 0)

    def test_verbatim_leak_is_caught(self):
        leaked = self.CLEAN + "\nPerson A told Person B that Person C is leaving the company at the end of the quarter.\n"
        self.assertEqual(self._run(leaked, self.WITHHELD), 1)

    def test_missing_notice_is_caught(self):
        # A silent hole reads as a complete record, which is worse than a visible gap.
        silent = self.CLEAN.replace("> [!warning] Withheld from archive\n> 1 block withheld: personnel.\n", "")
        self.assertEqual(self._run(silent, self.WITHHELD), 1)

    def test_tainted_filename_is_caught(self):
        # The filename never passes through the body gate, so a slug naming the subject
        # leaks it even when every block was withheld.
        self.assertEqual(
            self._run(self.CLEAN, self.WITHHELD, name="2026-05-04-personc-leaving.md"), 1
        )

    def test_unreadable_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp) / "w.md"
            w.write_text(self.WITHHELD)
            rc = subprocess.run(
                [sys.executable, str(VERIFIER), str(Path(tmp) / "missing.md"), str(w)],
                capture_output=True, text=True,
            ).returncode
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
        self.assertEqual(self._run(leaked, withheld), 1)

    def test_entity_named_only_in_withheld_block_is_caught(self):
        # Regression, and the leak this capability's own documentation calls the subtlest
        # it can produce: the body shows nothing while the frontmatter announces who the
        # withheld block was about. Only `source:` used to be checked.
        withheld = ("## Withheld from 2026-05-04-topic.md\n\n"
                    "**Confidential:** Zsuzsanna is being managed out after a review.\n")
        note = self.CLEAN.replace("redacted: true",
                                  'people: ["[[person/Zsuzsanna]]"]\nredacted: true')
        self.assertEqual(self._run(note, withheld), 1)

    def test_allow_permits_a_genuine_collision(self):
        # The sweep is deliberately biased toward flagging, so an escape hatch has to exist
        # or a legitimate shared word makes an archive permanently unrunnable.
        withheld = ("## Withheld from 2026-05-04-topic.md\n\n"
                    "**Confidential:** PersonC was fired.\n")
        leaked = self.CLEAN.replace("Ordinary content.", "Ordinary content. PersonC was fired.")
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "2026-05-04-topic.md"; a.write_text(leaked)
            w = Path(tmp) / "withheld.md"; w.write_text(withheld)
            rc = subprocess.run(
                [sys.executable, str(VERIFIER), str(a), str(w),
                 "--allow", "personc", "--allow", "fired", "--quiet"],
                capture_output=True, text=True,
            ).returncode
        self.assertEqual(rc, 0)

    def test_empty_withheld_file_fails_closed(self):
        # An empty redacted file means the withheld material was lost. Passing here
        # would let the source log be pruned, destroying the only remaining copy.
        self.assertEqual(self._run(self.CLEAN, ""), 2)


if __name__ == "__main__":
    unittest.main()
