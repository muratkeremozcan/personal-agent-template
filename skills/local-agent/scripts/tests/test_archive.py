#!/usr/bin/env python3
"""Tests for the archive tier: where aged logs go, and what stays behind on the machine.

The behaviours worth pinning here are the ones where a wrong answer looks like a right
one. An aged log reported with no destination reads as an instruction to delete. An
ignore rule that misses the withheld blocks pushes them to the git host with the rest of
the sanctum. The redaction gate itself is tested in test_verify_archive_redaction.py.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parents[2]
sys.path.insert(0, str(SCRIPTS))

import _sanctum  # noqa: E402


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


class IgnoreRulesTest(unittest.TestCase):
    """The repository is pushed to a git host, so .gitignore is part of what decides what
    leaves the machine. sessions/redacted/ holds exactly the blocks the archive gate
    withheld; references/archive.md promises it stays local."""

    IGNORED = (
        "_bmad/memory/local-agent/sessions/redacted/2026-05-04-topic.md",
        "_bmad/memory/local-agent/knowledge/private/notes.md",
        "_bmad/memory/local-agent/.runtime/state.json",
        "config/oauth_token.json",
        "application_default_credentials.json",
        "service-account-prod.json",
        "keys/id_rsa",
        "keys/id_ed25519.pub",
        "certs/client.p12",
        "certs/client.pfx",
        ".npmrc",
        ".pypirc",
        "imports/raw/export.json",
        "imports/chatgpt/export-2026.zip",
        ".env.local",
    )
    TRACKED = (
        "_bmad/memory/local-agent/MEMORY.md",
        "_bmad/memory/local-agent/sessions/2026-05-04-topic.md",
        "_bmad/memory/local-agent/INDEX.md",
        "imports/chatgpt/README.md",
        "skills/local-agent/scripts/tests/fixtures/redaction/redacted.md",
    )

    def check_ignore(self, path: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "--no-index", "-q", path],
            capture_output=True, text=True, check=False,
        )
        if result.returncode not in (0, 1):
            self.fail(f"git check-ignore failed for {path}: {result.stderr}")
        return result.returncode == 0

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        probe = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
        if probe.returncode != 0 or Path(probe.stdout.strip()).resolve() != REPO.resolve():
            self.skipTest("not running from a checkout of this repository")

    def test_private_material_is_ignored(self):
        for path in self.IGNORED:
            with self.subTest(path):
                self.assertTrue(self.check_ignore(path), f"{path} would be committed")

    def test_the_sanctum_itself_stays_tracked(self):
        for path in self.TRACKED:
            with self.subTest(path):
                self.assertFalse(self.check_ignore(path), f"{path} would be ignored")


if __name__ == "__main__":
    unittest.main()
