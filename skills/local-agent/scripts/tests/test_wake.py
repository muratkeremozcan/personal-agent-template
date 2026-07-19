#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for wake.py: mode detection and identity-file emission."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "wake.py"


class WakeTests(unittest.TestCase):
    def run_wake(self, project_root, home=None):
        """Invoke wake.py. `home`, if given, sets LOCAL_AGENT_HOME so the
        test controls sanctum location independently of project_root."""
        env = dict(os.environ)
        if home is not None:
            env["LOCAL_AGENT_HOME"] = str(home)
        else:
            env.pop("LOCAL_AGENT_HOME", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(project_root)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def _scaffold(self, home):
        sanctum = Path(home) / "_bmad" / "memory" / "local-agent"
        sanctum.mkdir(parents=True)
        (sanctum / "CREED.md").write_text("creed content")
        (sanctum / "MEMORY.md").write_text("memory content")
        (sanctum / "PERSONA.md").write_text("persona content")
        return sanctum

    def test_first_breath_when_no_sanctum(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            result = self.run_wake(project, home=home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: FIRST_BREATH", result.stdout)
            self.assertNotIn("MODE: FIRST_BREATH_RESUME", result.stdout)

    def test_resume_when_scaffolded_but_not_born(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            self._scaffold(home)  # no .born marker -> interrupted birth
            result = self.run_wake(project, home=home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: FIRST_BREATH_RESUME", result.stdout)
            self.assertNotIn("MODE: WAKING", result.stdout)

    def test_waking_when_born(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            sanctum = self._scaffold(home)
            (sanctum / ".born").write_text("2026-07-14")
            result = self.run_wake(project, home=home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: WAKING", result.stdout)
            self.assertIn("creed content", result.stdout)
            self.assertIn("(missing: INDEX.md)", result.stdout)

    def test_project_root_never_determines_sanctum_location(self):
        """Waking from an unrelated project must still find the canonical
        sanctum and must never trigger a false First Breath there."""
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as unrelated_project,
        ):
            sanctum = self._scaffold(home)
            (sanctum / ".born").write_text("2026-07-14")
            result = self.run_wake(unrelated_project, home=home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: WAKING", result.stdout)
            self.assertNotIn("MODE: FIRST_BREATH", result.stdout)
            self.assertIn(
                f"Invoked from: {Path(unrelated_project).resolve()}", result.stdout
            )
            self.assertIn(f"Sanctum: {sanctum.resolve()}", result.stdout)

    def test_default_home_used_when_no_override_set(self):
        """Without LOCAL_AGENT_HOME, falls back to ~/local-agent via $HOME."""
        with (
            tempfile.TemporaryDirectory() as fake_home,
            tempfile.TemporaryDirectory() as project,
        ):
            sanctum = (
                Path(fake_home) / "local-agent" / "_bmad" / "memory" / "local-agent"
            )
            sanctum.mkdir(parents=True)
            (sanctum / "CREED.md").write_text("creed")
            (sanctum / "MEMORY.md").write_text("memory")
            (sanctum / ".born").write_text("2026-07-14")

            env = dict(os.environ)
            env.pop("LOCAL_AGENT_HOME", None)
            env["HOME"] = fake_home
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(project)],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: WAKING", result.stdout)

    def test_missing_positional_arg_errors(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
