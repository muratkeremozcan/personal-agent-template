#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for curate.py: memory guardrail, stale-log detection, index drift."""

import importlib.util
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = SKILL_ROOT / "scripts" / "curate.py"

spec = importlib.util.spec_from_file_location("curate", SCRIPT_PATH)
curate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curate)


class CurateTests(unittest.TestCase):
    def _sanctum(self, tmp):
        s = Path(tmp) / "_bmad" / "memory" / "local-agent"
        (s / "sessions").mkdir(parents=True)
        (s / "capabilities").mkdir()
        return s

    def test_memory_guardrail_over_and_under(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sanctum(tmp)
            (s / "MEMORY.md").write_text("word " * 200)
            over = curate.measure_memory(s, guardrail=10)
            self.assertGreater(over["tokens"], 10)
            self.assertTrue(over["over_guardrail"])
            under = curate.measure_memory(s, guardrail=100_000)
            self.assertFalse(under["over_guardrail"])

    def test_stale_logs_by_filename_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sanctum(tmp)
            (s / "sessions" / "2020-01-01.md").write_text("old")
            (s / "sessions" / "2026-07-10.md").write_text("recent")
            r = curate.stale_session_logs(s, days=14, today=date(2026, 7, 14))
            self.assertEqual(r["total"], 2)
            self.assertIn("2020-01-01.md", r["stale"])
            self.assertNotIn("2026-07-10.md", r["stale"])

    def test_index_drift_flags_unlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self._sanctum(tmp)
            (s / "INDEX.md").write_text(
                "## My Files\n- reading-tracker.md: a tracker\n"
            )
            (s / "reading-tracker.md").write_text("listed")
            (s / "weekly-digest.md").write_text("not listed")
            (s / "capabilities" / "digest.md").write_text("not listed")
            (s / "knowledge").mkdir()
            (s / "knowledge" / "imported-context.md").write_text("not listed")
            r = curate.index_drift(s)
            self.assertIn("weekly-digest.md", r["unlisted"])
            self.assertIn("capabilities/digest.md", r["unlisted"])
            self.assertIn("knowledge/imported-context.md", r["unlisted"])
            self.assertNotIn("reading-tracker.md", r["unlisted"])

    def test_main_no_sanctum_returns_1(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            with mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": home}):
                self.assertEqual(curate.main([project]), 1)

    def test_main_reports_returns_0(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            s = self._sanctum(home)
            (s / "MEMORY.md").write_text("hi")
            with mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": home}):
                self.assertEqual(curate.main([project]), 0)

    def test_main_ignores_project_root_for_sanctum_location(self):
        """project_root is informational only; sanctum always resolves via
        LOCAL_AGENT_HOME or the ~/local-agent default, never cwd."""
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as unrelated_project,
        ):
            s = self._sanctum(home)
            (s / "MEMORY.md").write_text("hi")
            with mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": home}):
                self.assertEqual(curate.main([unrelated_project]), 0)


if __name__ == "__main__":
    unittest.main()
