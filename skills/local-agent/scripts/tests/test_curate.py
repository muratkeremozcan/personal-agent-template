#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for curate.py: memory guardrail, stale-log detection, index drift."""

import contextlib
import importlib.util
import io
import json
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

import _sanctum  # noqa: E402  importable once curate.py has put scripts/ on sys.path

RETENTION = _sanctum.SESSION_RETENTION_DAYS
TODAY = date(2026, 7, 14)
FALLBACK_REASON = "tiktoken patched out for tests"


class FakeEncoder:
    """Counts one token per whitespace-separated word, so counts are predictable."""

    def encode(self, text):
        return text.split()


class CurateCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.project = Path(project.name).resolve()

        env = mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LOCAL_AGENT_ARCHIVE", None)
        # The real encoder downloads cl100k_base on first use when tiktoken is
        # installed, which a unit test must never depend on.
        encoder = mock.patch.object(curate, "_encoder", return_value=(None, FALLBACK_REASON))
        self.encoder = encoder.start()
        self.addCleanup(encoder.stop)

    def make_sanctum(self):
        s = self.home / "_bmad" / "memory" / "local-agent"
        (s / "sessions").mkdir(parents=True)
        (s / "capabilities").mkdir()
        return s

    def run_main(self, *args):
        """Run main() with captured output: (exit code, parsed JSON or None, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = curate.main([str(self.project), *args])
            except SystemExit as exc:
                code = exc.code
        try:
            report = json.loads(out.getvalue())
        except json.JSONDecodeError:
            report = None
        return code, report, err.getvalue()


class MeasureTests(CurateCase):
    def test_memory_guardrail_over_and_under(self):
        s = self.make_sanctum()
        (s / "MEMORY.md").write_text("word " * 200)
        over = curate.measure_memory(s, guardrail=10)
        self.assertEqual(over["tokens"], len("word " * 200) // 4)
        self.assertTrue(over["over_guardrail"])
        under = curate.measure_memory(s, guardrail=100_000)
        self.assertFalse(under["over_guardrail"])

    def test_tiktoken_counts_are_used_when_the_encoder_loads(self):
        self.encoder.return_value = (FakeEncoder(), None)
        s = self.make_sanctum()
        (s / "MEMORY.md").write_text("one two three four five")
        memory = curate.measure_memory(s, guardrail=10)
        self.assertEqual((memory["tokens"], memory["method"]), (5, "tiktoken"))
        self.assertEqual(curate.tokenizer_status(), {"method": "tiktoken", "reason": None})

    def test_stale_logs_by_filename_date(self):
        s = self.make_sanctum()
        (s / "sessions" / "2020-01-01.md").write_text("old")
        (s / "sessions" / "2026-07-10.md").write_text("recent")
        r = curate.stale_session_logs(s, days=RETENTION, today=TODAY)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["stale"], ["2020-01-01.md"])


class DispositionTests(CurateCase):
    """What curate tells the agent to do with aged logs, per archive state."""

    def setUp(self):
        super().setUp()
        self.s = self.make_sanctum()
        (self.s / "sessions" / "2020-01-01-topic.md").write_text("old")
        (self.s / "sessions" / "2020-02-03.md").write_text("old")
        (self.s / "sessions" / "2026-07-10.md").write_text("recent")
        (self.s / "sessions" / "notes.md").write_text("no date")

    def logs(self):
        return curate.stale_session_logs(self.s, days=RETENTION, today=TODAY)

    def test_no_archive_configured(self):
        r = self.logs()
        self.assertEqual(r["disposition"], "no archive configured")
        self.assertEqual(r["stale"], ["2020-01-01-topic.md", "2020-02-03.md"])
        self.assertEqual(r["undated"], ["notes.md"])
        self.assertIn(str(self.home / "archive"), r["note"])
        self.assertIn(str(curate.ARCHIVE_PROCEDURE), r["note"])
        self.assertNotIn("destination", r)

    def test_archive_configured_gives_each_aged_log_a_destination(self):
        archive = self.home / "archive"
        archive.mkdir()
        os.environ["LOCAL_AGENT_ARCHIVE"] = str(archive)
        r = self.logs()
        self.assertEqual(r["disposition"], "archive")
        self.assertEqual(r["archive_root"], str(archive))
        self.assertEqual(
            r["destination"],
            [
                {"log": "2020-01-01-topic.md",
                 "archives_to": str(archive / "log" / "2020" / "01" / "2020-01-01-topic.md")},
                {"log": "2020-02-03.md",
                 "archives_to": str(archive / "log" / "2020" / "02" / "2020-02-03.md")},
            ],
        )
        self.assertEqual(r["undated"], ["notes.md"])
        self.assertIn("never become stale", r["undated_note"])

    def test_procedure_and_gate_are_absolute_and_exist(self):
        archive = self.home / "archive"
        archive.mkdir()
        os.environ["LOCAL_AGENT_ARCHIVE"] = str(archive)
        r = self.logs()
        for key in ("procedure", "gate"):
            path = Path(r[key])
            self.assertTrue(path.is_absolute(), r[key])
            self.assertTrue(path.is_file(), r[key])

    def test_mistyped_archive_is_misconfigured(self):
        typo = self.home / "archvie"
        os.environ["LOCAL_AGENT_ARCHIVE"] = str(typo)
        r = self.logs()
        self.assertEqual(r["disposition"], "misconfigured")
        self.assertIn(str(typo), r["error"])
        self.assertIn("Prune nothing", r["note"])
        self.assertEqual(r["stale"], ["2020-01-01-topic.md", "2020-02-03.md"])
        self.assertNotIn("destination", r)


class IndexDriftTests(CurateCase):
    def test_index_drift_flags_unlisted(self):
        s = self.make_sanctum()
        (s / "INDEX.md").write_text("## My Files\n- reading-tracker.md: a tracker\n")
        (s / "reading-tracker.md").write_text("listed")
        (s / "weekly-digest.md").write_text("not listed")
        (s / "capabilities" / "digest.md").write_text("not listed")
        (s / "knowledge").mkdir()
        (s / "knowledge" / "imported-context.md").write_text("not listed")
        r = curate.index_drift(s)
        self.assertEqual(
            r["unlisted"],
            ["capabilities/digest.md", "knowledge/imported-context.md", "weekly-digest.md"],
        )

    def test_a_longer_name_containing_the_file_does_not_list_it(self):
        s = self.make_sanctum()
        (s / "INDEX.md").write_text(
            "- `meeting-notes.md` — meetings\n- see plan.md.bak for the old plan\n"
        )
        for name in ("meeting-notes.md", "notes.md", "plan.md"):
            (s / name).write_text("x")
        self.assertEqual(curate.index_drift(s)["unlisted"], ["notes.md", "plan.md"])

    def test_names_listed_with_paths_and_punctuation_count(self):
        s = self.make_sanctum()
        (s / "INDEX.md").write_text(
            "- `knowledge/notes.md`\n- (see todo.md). Also projects/, and digest.md.\n"
        )
        (s / "todo.md").write_text("x")
        (s / "digest.md").write_text("x")
        (s / "projects").mkdir()
        (s / "knowledge").mkdir()
        (s / "knowledge" / "notes.md").write_text("x")
        self.assertEqual(curate.index_drift(s)["unlisted"], [])

    def test_index_drift_ignores_bytecode_caches_in_capabilities(self):
        s = self.make_sanctum()
        (s / "INDEX.md").write_text("- capabilities/real.md\n")
        (s / "capabilities" / "real.md").write_text("listed")
        (s / "capabilities" / "__pycache__").mkdir()
        (s / "capabilities" / ".DS_Store").write_text("")
        self.assertEqual(curate.index_drift(s)["unlisted"], [])


class MainTests(CurateCase):
    def test_no_sanctum_returns_1(self):
        code, report, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "no sanctum")
        self.assertEqual(report["sanctum"], str(self.home / "_bmad" / "memory" / "local-agent"))

    def test_report_names_the_canonical_sanctum_and_the_unrelated_project(self):
        """project_root is informational only; the sanctum resolves from the home."""
        s = self.make_sanctum()
        (s / "MEMORY.md").write_text("hi")
        (s / ".born").write_text("2026-07-01\n")
        code, report, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(report["sanctum"], str(s))
        self.assertEqual(report["invoked_from"], str(self.project))
        self.assertTrue(report["born"])
        self.assertEqual(report["born_on"], "2026-07-01")
        self.assertNotIn("born_warning", report)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(
            report["missing_identity_files"],
            [n for n in _sanctum.IDENTITY_FILES if n != "MEMORY.md"],
        )

    def test_tokenizer_fallback_is_stated_in_the_report_and_on_stderr(self):
        """An estimate that reads as an exact count is worse than no count."""
        (self.make_sanctum() / "MEMORY.md").write_text("hi")
        code, report, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(report["tokenizer"], {"method": "fallback", "reason": FALLBACK_REASON})
        self.assertEqual(report["memory_md"]["method"], "fallback")
        self.assertIn(f"warning: {FALLBACK_REASON}", err)

    def test_dateless_born_marker_is_reported(self):
        s = self.make_sanctum()
        (s / ".born").write_text("yes\n")
        code, report, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(report["born"])
        self.assertIsNone(report["born_on"])
        self.assertEqual(report["born_warning"], _sanctum.BORN_UNDATED_NOTE)

    def test_invalid_utf8_is_read_and_named(self):
        s = self.make_sanctum()
        (s / "INDEX.md").write_bytes(b"# Index\n- caf\xe9.md\n")
        (s / "caf�.md").write_text("x")
        code, report, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn(str(s / "INDEX.md"), report["warnings"][0])
        self.assertIn(f"warning: {s / 'INDEX.md'} is not valid UTF-8", err)
        self.assertEqual(report["index_md"]["lines"], 2)

    def test_non_positive_thresholds_are_usage_errors(self):
        self.make_sanctum()
        for flag in ("--days", "--guardrail", "--wake-budget"):
            for value in ("0", "-1", "ten"):
                with self.subTest(flag=flag, value=value):
                    code, report, err = self.run_main(flag, value)
                    self.assertEqual(code, 2)
                    self.assertIsNone(report)
                    self.assertIn(f"argument {flag}", err)

    def test_misconfigured_home_exits_2(self):
        os.environ["LOCAL_AGENT_HOME"] = "relative/home"
        code, report, _ = self.run_main()
        self.assertEqual(code, 2)
        self.assertEqual(report["error"], "LOCAL_AGENT_HOME misconfigured")
        self.assertIn("relative path", report["detail"])

    def test_help_says_aged_logs_are_archived_before_pruning(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            curate.main(["--help"])
        self.assertIn("archived before their source is pruned", out.getvalue())
        self.assertNotIn("can be pruned", out.getvalue())


if __name__ == "__main__":
    unittest.main()
