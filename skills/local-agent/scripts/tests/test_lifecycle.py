#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""End-to-end test for First Breath, resume, waking, and curation."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL_ROOT / "scripts"


class LifecycleTests(unittest.TestCase):
    def run_script(self, name, *args, home=None):
        env = dict(os.environ)
        if home is not None:
            env["LOCAL_AGENT_HOME"] = str(home)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), *map(str, args)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_full_lifecycle_from_an_unrelated_project(self):
        with tempfile.TemporaryDirectory() as home_dir:
            with tempfile.TemporaryDirectory() as active_project_dir:
                home = Path(home_dir)
                active_project = Path(active_project_dir)
                (home / "_bmad").mkdir()

                before_init = self.run_script("wake.py", active_project, home=home)
                self.assertEqual(before_init.returncode, 0)
                self.assertIn("MODE: FIRST_BREATH", before_init.stdout)

                initialized = self.run_script("init-sanctum.py", home, SKILL_ROOT)
                self.assertEqual(initialized.returncode, 0, initialized.stderr)

                resume = self.run_script("wake.py", active_project, home=home)
                self.assertEqual(resume.returncode, 0)
                self.assertIn("MODE: FIRST_BREATH_RESUME", resume.stdout)

                sanctum = home / "_bmad" / "memory" / "local-agent"
                (sanctum / "PERSONA.md").write_text(
                    "# Persona\n\n## Identity\n\n- **Name:** Example\n"
                )
                (sanctum / ".born").write_text("2026-07-19\n")

                waking = self.run_script("wake.py", active_project, home=home)
                self.assertEqual(waking.returncode, 0)
                self.assertIn("MODE: WAKING", waking.stdout)
                self.assertIn("**Name:** Example", waking.stdout)
                self.assertIn(
                    f"Invoked from: {active_project.resolve()}", waking.stdout
                )
                self.assertIn(f"Sanctum: {sanctum.resolve()}", waking.stdout)

                curated = self.run_script("curate.py", active_project, home=home)
                self.assertEqual(curated.returncode, 0, curated.stderr)
                report = json.loads(curated.stdout)
                self.assertTrue(report["born"])
                self.assertEqual(report["sanctum"], str(sanctum.resolve()))
                self.assertEqual(report["invoked_from"], str(active_project.resolve()))


if __name__ == "__main__":
    unittest.main()
