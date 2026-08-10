#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for the curation guardrail: _sanctum.health and wake.py's notice.

The bug these guard against is the one that actually happened: MEMORY.md carried
the only guardrail, INDEX.md grew to three times its size unmeasured, and nothing
fired because nothing was scheduled to look.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _sanctum  # noqa: E402

WAKE = Path(__file__).resolve().parent.parent / "wake.py"


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
        (sanctum / _sanctum.BORN_MARKER).write_text("2026-07-14")
    return sanctum


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
            report = _sanctum.health(sanctum)
            self.assertFalse(report["curation_due"])
            self.assertEqual(report["reasons"], [])

    def test_oversized_memory_is_caught(self):
        with tempfile.TemporaryDirectory() as home:
            oversized = "word " * (_sanctum.MEMORY_GUARDRAIL_TOKENS * _sanctum.BYTES_PER_TOKEN)
            sanctum = scaffold(home, memory=oversized)
            report = _sanctum.health(sanctum)
            self.assertTrue(report["curation_due"])
            self.assertTrue(any("MEMORY.md" in r for r in report["reasons"]))

    def test_index_carrying_prose_is_caught(self):
        """The regression that started this: an index that became a memory file."""
        with tempfile.TemporaryDirectory() as home:
            fat = "\n".join(f"- `f{i}.md` — " + ("detail " * 40) for i in range(5))
            sanctum = scaffold(home, index="# Index\n" + fat)
            report = _sanctum.health(sanctum)
            self.assertTrue(report["curation_due"])
            self.assertEqual(report["fat_index_entries"], 5)
            self.assertTrue(any("belong in the file" in r for r in report["reasons"]))

    def test_index_of_pure_pointers_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as home:
            lean = "\n".join(f"- `f{i}.md` — a short hook" for i in range(200))
            sanctum = scaffold(home, index="# Index\n" + lean)
            report = _sanctum.health(sanctum)
            self.assertEqual(report["fat_index_entries"], 0)

    def test_aged_logs_are_caught_and_first_breath_is_exempt(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            old = date.today() - timedelta(days=_sanctum.SESSION_RETENTION_DAYS + 5)
            (sanctum / "sessions" / f"{old.isoformat()}-topic.md").write_text("x")
            (sanctum / "sessions" / "2026-07-14.md").write_text("first breath")
            aged = _sanctum.stale_logs(sanctum, _sanctum.SESSION_RETENTION_DAYS, date.today())
            self.assertIn(f"{old.isoformat()}-topic.md", aged)
            self.assertNotIn("2026-07-14.md", aged)

    def test_fresh_logs_are_left_alone(self):
        with tempfile.TemporaryDirectory() as home:
            sanctum = scaffold(home)
            recent = date.today() - timedelta(days=1)
            (sanctum / "sessions" / f"{recent.isoformat()}-topic.md").write_text("x")
            self.assertEqual(
                _sanctum.stale_logs(sanctum, _sanctum.SESSION_RETENTION_DAYS, date.today()), []
            )


class WakeNoticeTests(unittest.TestCase):
    def run_wake(self, home, project):
        env = dict(os.environ)
        env["LOCAL_AGENT_HOME"] = str(home)
        return subprocess.run(
            [sys.executable, str(WAKE), str(project)],
            capture_output=True, text=True, check=False, env=env,
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

    def test_notice_never_fires_during_first_breath(self):
        """A newborn has nothing to curate; a nag there would be nonsense."""
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project:
            out = self.run_wake(home, project)
            self.assertIn("MODE: FIRST_BREATH", out.stdout)
            self.assertNotIn("CURATION DUE", out.stdout)


if __name__ == "__main__":
    unittest.main()
