#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the curation guardrail: _sanctum.health and wake.py's notice.

The bug these guard against is the one that actually happened: MEMORY.md carried
the only guardrail, INDEX.md grew to three times its size unmeasured, and nothing
fired because nothing was scheduled to look.
"""

import contextlib
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _sanctum

WAKE = Path(__file__).resolve().parent.parent / "wake.py"
RETENTION = _sanctum.SESSION_RETENTION_DAYS
# A fixed clock, so no assertion depends on the day the suite runs.
TODAY = date(2026, 8, 1)
BIRTH = TODAY - timedelta(days=RETENTION + 4)


def scaffold(home, *, index="", memory="", born=True):
    sanctum = Path(home) / "_bmad" / "memory" / "local-agent"
    (sanctum / "sessions").mkdir(parents=True)
    for name in _sanctum.IDENTITY_FILES:
        (sanctum / name).write_text("x")
    if index:
        (sanctum / "INDEX.md").write_text(index)
    if memory:
        (sanctum / "MEMORY.md").write_text(memory)
    if born:
        (sanctum / _sanctum.BORN_MARKER).write_text(BIRTH.isoformat())
    return sanctum


def isolated_env(home):
    """Process environment for a subprocess that must see only the test's home."""
    env = dict(os.environ)
    env.pop("LOCAL_AGENT_ARCHIVE", None)
    env["LOCAL_AGENT_HOME"] = str(home)
    return env


class ProseOfTests(unittest.TestCase):
    def test_backticked_pointers_do_not_count_as_prose(self):
        cluster = "- **pr-gate** — " + ", ".join(f"`{i:03d}-some-session-log`" for i in range(20))
        self.assertGreater(len(cluster), _sanctum.INDEX_ENTRY_DEMOTE_CHARS)
        self.assertLess(len(_sanctum.prose_of(cluster)), _sanctum.INDEX_ENTRY_TARGET_CHARS)

    def test_prose_still_counts(self):
        entry = "- `file.md` — " + ("word " * 80)
        self.assertGreater(len(_sanctum.prose_of(entry)), _sanctum.INDEX_ENTRY_DEMOTE_CHARS)


class HealthTests(unittest.TestCase):
    def test_healthy_sanctum_reports_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home, index="# Index\n- `a.md` — short hook\n")
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertFalse(report["curation_due"])
            self.assertEqual(report["reasons"], [])

    def test_oversized_memory_is_caught(self):
        with tempfile.TemporaryDirectory() as home:
            oversized = "word " * (_sanctum.MEMORY_GUARDRAIL_TOKENS * _sanctum.BYTES_PER_TOKEN)
            sanctum = scaffold(home, memory=oversized)
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertTrue(report["curation_due"])
            self.assertTrue(any("MEMORY.md" in r for r in report["reasons"]))

    def test_index_carrying_prose_is_caught(self):
        """The regression that started this: an index that became a memory file."""
        with tempfile.TemporaryDirectory() as home:
            fat = "\n".join(f"- `f{i}.md` — " + ("detail " * 40) for i in range(5))
            sanctum = scaffold(home, index="# Index\n" + fat)
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertTrue(report["curation_due"])
            self.assertEqual(report["fat_index_entries"], 5)
            self.assertTrue(any("belong in the file" in r for r in report["reasons"]))

    def test_pointer_entries_are_not_counted_as_fat(self):
        with tempfile.TemporaryDirectory() as home:
            lean = "\n".join(f"- `f{i}.md` — a short hook" for i in range(150))
            sanctum = scaffold(home, index="# Index\n" + lean)
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertEqual(report["fat_index_entries"], 0)
            self.assertEqual(report["reasons"], [])

    def test_index_over_the_line_ceiling_is_the_only_reason(self):
        with tempfile.TemporaryDirectory() as home:
            lines = "\n".join(f"- `f{i}.md`" for i in range(_sanctum.INDEX_MAX_LINES + 1))
            sanctum = scaffold(home, index=lines)
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertEqual(report["index_lines"], _sanctum.INDEX_MAX_LINES + 1)
            self.assertEqual(
                report["reasons"],
                [(f"INDEX.md is {_sanctum.INDEX_MAX_LINES + 1} lines against a "
                  f"{_sanctum.INDEX_MAX_LINES} ceiling")],
            )

    def test_index_over_the_byte_ceiling_is_the_only_reason(self):
        """Few lines, none of them entries, so only the byte ceiling can fire."""
        with tempfile.TemporaryDirectory() as home:
            paragraph = "p" * 3000
            index = "\n".join([paragraph] * 9)  # ~27KB over 9 lines
            sanctum = scaffold(home, index=index)
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertGreater(report["index_bytes"], _sanctum.INDEX_MAX_BYTES)
            self.assertEqual(
                report["reasons"],
                [(f"INDEX.md is {report['index_bytes'] // 1024}KB against a "
                  f"{_sanctum.INDEX_MAX_BYTES // 1024}KB ceiling")],
            )

    def test_waking_load_over_budget_is_the_only_reason(self):
        """Spread across files no other guardrail measures, so only the budget fires."""
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            share = (_sanctum.WAKE_BUDGET_TOKENS * _sanctum.BYTES_PER_TOKEN) // 3 + 100
            for name in ("PERSONA.md", "CREED.md", "BOND.md"):
                (sanctum / name).write_text("y" * share)
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertGreater(report["estimated_wake_tokens"], _sanctum.WAKE_BUDGET_TOKENS)
            self.assertEqual(
                report["reasons"],
                [(f"waking costs ~{report['estimated_wake_tokens']:,} tokens against a "
                  f"{_sanctum.WAKE_BUDGET_TOKENS:,} budget")],
            )

    def test_aged_logs_are_caught_and_first_breath_is_exempt(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            old = TODAY - timedelta(days=RETENTION + 1)
            (sanctum / "sessions" / f"{old.isoformat()}-topic.md").write_text("x")
            (sanctum / "sessions" / f"{BIRTH.isoformat()}.md").write_text("first breath")
            aged = _sanctum.stale_logs(sanctum, RETENTION, TODAY)
            self.assertEqual(aged, [f"{old.isoformat()}-topic.md"])
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertEqual(report["aged_logs"], aged)
            self.assertIn(
                f"1 session logs are past {RETENTION} days and await distilling",
                report["reasons"],
            )

    def test_a_log_exactly_at_the_threshold_is_not_aged(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            edge = TODAY - timedelta(days=RETENTION)
            (sanctum / "sessions" / f"{edge.isoformat()}-topic.md").write_text("x")
            self.assertEqual(_sanctum.stale_logs(sanctum, RETENTION, TODAY), [])

    def test_only_the_first_breath_log_is_exempt_on_the_birth_date(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            (sanctum / "sessions" / f"{BIRTH.isoformat()}.md").write_text("first breath")
            (sanctum / "sessions" / f"{BIRTH.isoformat()}-second-topic.md").write_text("later")
            aged = _sanctum.stale_logs(sanctum, RETENTION, TODAY)
            self.assertEqual(aged, [f"{BIRTH.isoformat()}-second-topic.md"])

    def test_fresh_logs_are_left_alone(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            recent = TODAY - timedelta(days=1)
            (sanctum / "sessions" / f"{recent.isoformat()}-topic.md").write_text("x")
            self.assertEqual(_sanctum.stale_logs(sanctum, RETENTION, TODAY), [])

    def test_born_marker_without_a_date_is_reported(self):
        """A dateless .born silently cost the First Breath log its exemption."""
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            (sanctum / _sanctum.BORN_MARKER).write_text("born!\n")
            (sanctum / "sessions" / f"{BIRTH.isoformat()}.md").write_text("first breath")
            report = _sanctum.health(sanctum, today=TODAY)
            self.assertTrue(report["born_undated"])
            self.assertIn(_sanctum.BORN_UNDATED_NOTE, report["reasons"])
            self.assertEqual(report["aged_logs"], [f"{BIRTH.isoformat()}.md"])

    def test_dated_born_marker_is_not_reported(self):
        with tempfile.TemporaryDirectory() as home:
            report = _sanctum.health(scaffold(home), today=TODAY)
            self.assertFalse(report["born_undated"])

    def test_invalid_utf8_in_index_is_read_and_named(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            (sanctum / "INDEX.md").write_bytes(b"# Index\n- caf\xe9\n")
            _sanctum.DECODE_WARNINGS.clear()
            self.addCleanup(_sanctum.DECODE_WARNINGS.clear)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                report = _sanctum.health(sanctum, today=TODAY)
                _sanctum.health(sanctum, today=TODAY)
            self.assertEqual(report["index_lines"], 2)
            self.assertEqual(len(_sanctum.DECODE_WARNINGS), 1)
            self.assertIn(str(sanctum / "INDEX.md"), _sanctum.DECODE_WARNINGS[0])
            self.assertEqual(err.getvalue().count("warning:"), 1)


class HomeTests(unittest.TestCase):
    def home_with(self, value):
        with mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": value}):
            return _sanctum.sanctum_home()

    def test_existing_absolute_override_is_used(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertEqual(self.home_with(home), Path(home).resolve())

    def test_relative_override_is_refused(self):
        with self.assertRaisesRegex(_sanctum.HomeMisconfigured, "relative path"):
            self.home_with("local-agent")

    def test_override_naming_a_missing_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            typo = Path(parent) / "local-agnet"
            with self.assertRaisesRegex(_sanctum.HomeMisconfigured, "not an existing directory"):
                self.home_with(str(typo))

    def test_override_naming_a_file_is_refused(self):
        with tempfile.TemporaryDirectory() as parent:
            not_a_dir = Path(parent) / "file"
            not_a_dir.write_text("")
            with self.assertRaises(_sanctum.HomeMisconfigured):
                self.home_with(str(not_a_dir))

    def test_default_home_may_not_exist_yet(self):
        """Nobody mistypes a value they never set, so a missing default is a first birth."""
        with tempfile.TemporaryDirectory() as fake_home:
            env = {"HOME": fake_home, "USERPROFILE": fake_home}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("LOCAL_AGENT_HOME", None)
                self.assertEqual(
                    _sanctum.sanctum_home(), (Path(fake_home) / "local-agent").resolve()
                )


class ArchiveOverlapTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.sanctum = self.home / "_bmad" / "memory" / "local-agent"
        (self.sanctum / "sessions").mkdir(parents=True)
        if not (self.home / "_BMAD" / "memory" / "Local-Agent").is_dir():
            self.skipTest("case-sensitive filesystem: a case-swapped path is a different path")

    def archive_at(self, path):
        env = {"LOCAL_AGENT_HOME": str(self.home), "LOCAL_AGENT_ARCHIVE": str(path)}
        with mock.patch.dict(os.environ, env):
            return _sanctum.archive_root()

    def test_case_swapped_path_inside_the_sanctum_is_rejected(self):
        with self.assertRaisesRegex(_sanctum.ArchiveMisconfigured, "overlaps the sanctum"):
            self.archive_at(self.home / "_BMAD" / "memory" / "Local-Agent" / "sessions")

    def test_case_swapped_home_is_rejected(self):
        swapped_home = Path(str(self.home).swapcase())
        with self.assertRaisesRegex(_sanctum.ArchiveMisconfigured, "overlaps the sanctum"):
            self.archive_at(swapped_home / "_bmad" / "memory" / "local-agent" / "sessions")

    def test_case_swapped_sanctum_itself_is_rejected(self):
        with self.assertRaisesRegex(_sanctum.ArchiveMisconfigured, "overlaps the sanctum"):
            self.archive_at(self.home / "_BMAD" / "memory" / "Local-Agent")

    def test_case_swapped_parent_of_the_sanctum_is_rejected(self):
        with self.assertRaisesRegex(_sanctum.ArchiveMisconfigured, "overlaps the sanctum"):
            self.archive_at(self.home / "_BMAD")

    def test_sibling_of_the_sanctum_is_accepted(self):
        archive = self.home / "archive"
        archive.mkdir()
        self.assertEqual(self.archive_at(archive), archive)


class WakeNoticeTests(unittest.TestCase):
    def run_wake(self, home, project):
        return subprocess.run(
            [sys.executable, str(WAKE), str(project)],
            capture_output=True, text=True, check=False, env=isolated_env(home),
        )

    def test_notice_absent_when_healthy(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            scaffold(home, index="# Index\n- `a.md` — short hook\n")
            out = self.run_wake(home, project)
            self.assertEqual(out.returncode, 0)
            self.assertIn("MODE: WAKING", out.stdout)
            self.assertNotIn("CURATION DUE", out.stdout)

    def test_notice_fires_when_over_threshold(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            oversized = "word " * (_sanctum.MEMORY_GUARDRAIL_TOKENS * _sanctum.BYTES_PER_TOKEN)
            scaffold(home, memory=oversized)
            out = self.run_wake(home, project)
            self.assertEqual(out.returncode, 0)
            self.assertIn("CURATION DUE", out.stdout)
            self.assertIn("references/curation-pass.md", out.stdout)
            # It must not hijack the session it fires in.
            self.assertIn("Do not derail", out.stdout)

    def test_notice_names_a_dateless_born_marker(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            sanctum = scaffold(home)
            (sanctum / _sanctum.BORN_MARKER).write_text("done")
            out = self.run_wake(home, project)
            self.assertEqual(out.returncode, 0)
            self.assertIn("MODE: WAKING", out.stdout)
            self.assertIn(f"- {_sanctum.BORN_UNDATED_NOTE}", out.stdout)

    def test_printed_curate_command_runs_from_the_project_directory(self):
        """The notice is read and executed from wherever the session opened."""
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            oversized = "word " * (_sanctum.MEMORY_GUARDRAIL_TOKENS * _sanctum.BYTES_PER_TOKEN)
            scaffold(home, memory=oversized)
            out = self.run_wake(home, project)
            line = next(
                ln for ln in out.stdout.splitlines() if ln.startswith("Exact token counts:")
            )
            script, target = shlex.split(line.split("uv run ", 1)[1])
            self.assertTrue(Path(script).is_absolute(), script)
            ran = subprocess.run(
                [sys.executable, script, target],
                capture_output=True, text=True, check=False, cwd=project,
                env=isolated_env(home),
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)

    def test_notice_never_fires_during_first_breath(self):
        """A newborn has nothing to curate; a nag there would be nonsense."""
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            out = self.run_wake(home, project)
            self.assertIn("MODE: FIRST_BREATH", out.stdout)
            self.assertNotIn("CURATION DUE", out.stdout)


if __name__ == "__main__":
    unittest.main()
