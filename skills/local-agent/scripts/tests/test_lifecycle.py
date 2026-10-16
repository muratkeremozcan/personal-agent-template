#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""End-to-end tests for First Breath, resume, waking, curation, and recovery."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL_ROOT / "scripts"


def run_script(name, *args, home):
    """Run a lifecycle script as the agent does, against an isolated home."""
    env = dict(os.environ)
    env.pop("LOCAL_AGENT_ARCHIVE", None)
    env["LOCAL_AGENT_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name).resolve()
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.project = Path(project.name).resolve()
        self.sanctum = self.home / "_bmad" / "memory" / "local-agent"

    def wake(self):
        return run_script("wake.py", self.project, home=self.home)

    def init(self, skill=SKILL_ROOT):
        return run_script("init-sanctum.py", self.project, skill, home=self.home)

    def test_full_lifecycle_from_an_unrelated_project(self):
        (self.home / "_bmad").mkdir()

        before_init = self.wake()
        self.assertEqual(before_init.returncode, 0, msg="wake before init")
        self.assertIn("MODE: FIRST_BREATH", before_init.stdout, msg="wake before init")

        # First Breath runs from the active project, as it does for a real
        # owner. The sanctum must still land in the canonical home, or the
        # next wake finds nothing and First Breath starts over.
        initialized = self.init()
        self.assertEqual(initialized.returncode, 0, msg=f"init: {initialized.stderr}")

        resume = self.wake()
        self.assertEqual(resume.returncode, 0, msg="wake after init")
        self.assertIn("MODE: FIRST_BREATH_RESUME", resume.stdout, msg="wake after init")
        self.assertFalse((self.project / "_bmad").exists(), msg="init wrote into the project")

        (self.sanctum / "PERSONA.md").write_text(
            "# Persona\n\n## Identity\n\n- **Name:** Example\n"
        )
        (self.sanctum / ".born").write_text("2026-07-19\n")

        waking = self.wake()
        self.assertEqual(waking.returncode, 0, msg="wake after birth")
        self.assertIn("MODE: WAKING", waking.stdout, msg="wake after birth")
        self.assertIn("**Name:** Example", waking.stdout, msg="wake after birth")
        self.assertIn(f"Invoked from: {self.project}", waking.stdout, msg="wake after birth")
        self.assertIn(f"Sanctum: {self.sanctum}", waking.stdout, msg="wake after birth")

        curated = run_script("curate.py", self.project, home=self.home)
        self.assertEqual(curated.returncode, 0, msg=f"curate: {curated.stderr}")
        report = json.loads(curated.stdout)
        self.assertTrue(report["born"], msg="curate report")
        self.assertEqual(report["sanctum"], str(self.sanctum), msg="curate report")
        self.assertEqual(report["invoked_from"], str(self.project), msg="curate report")
        self.assertEqual(report["missing_identity_files"], [], msg="curate report")

    def test_a_failed_scaffold_can_be_retried(self):
        """A bad skill path once left folders that made every retry a no-op."""
        failed = self.init(skill=self.project / "nonexistent-skill")
        self.assertEqual(failed.returncode, 2, msg="init with a bad skill path")
        self.assertIn("is not a local-agent skill directory", failed.stderr, msg="init stderr")
        self.assertNotIn("Traceback", failed.stderr, msg="init stderr")
        self.assertFalse(self.sanctum.exists(), msg="sanctum after the failed init")

        after_failure = self.wake()
        self.assertEqual(after_failure.returncode, 0, msg="wake after the failed init")
        self.assertIn("MODE: FIRST_BREATH", after_failure.stdout, msg="wake after failed init")

        retried = self.init()
        self.assertEqual(retried.returncode, 0, msg=f"init retry: {retried.stderr}")
        self.assertNotIn("Skipping", retried.stdout, msg="init retry")
        self.assertIn("MODE: FIRST_BREATH_RESUME", self.wake().stdout, msg="wake after retry")

    def test_a_bundle_missing_a_template_publishes_nothing(self):
        """A pruned bundle once published a sanctum without CREED.md and exited 0."""
        templates = sorted(p.name for p in (SKILL_ROOT / "assets").glob("*-template.md"))
        self.assertEqual(len(templates), 5, msg="template inventory")
        for template in templates:
            with self.subTest(template=template), tempfile.TemporaryDirectory() as tmp:
                bundle = Path(tmp) / "local-agent"
                for folder in ("assets", "references", "scripts"):
                    shutil.copytree(
                        SKILL_ROOT / folder, bundle / folder,
                        ignore=shutil.ignore_patterns("tests", "__pycache__"),
                    )
                held = Path(tmp) / template
                (bundle / "assets" / template).rename(held)

                failed = self.init(skill=bundle)
                self.assertEqual(failed.returncode, 2, msg=f"init without {template}")
                self.assertIn(template, failed.stderr, msg=f"init stderr without {template}")
                self.assertNotIn("Traceback", failed.stderr, msg=f"init without {template}")
                self.assertFalse(
                    os.path.lexists(self.sanctum), msg=f"sanctum after init without {template}"
                )
                self.assertEqual(
                    list(self.sanctum.parent.glob("*")) if self.sanctum.parent.exists() else [],
                    [],
                    msg=f"staging left after init without {template}",
                )
                self.assertIn(
                    "MODE: FIRST_BREATH\n", self.wake().stdout, msg=f"wake without {template}"
                )

                held.rename(bundle / "assets" / template)
                restored = self.init(skill=bundle)
                self.assertEqual(
                    restored.returncode, 0, msg=f"init after restoring {template}: "
                    f"{restored.stderr}"
                )
                self.assertIn(
                    "MODE: FIRST_BREATH_RESUME", self.wake().stdout,
                    msg=f"wake after restoring {template}",
                )
                shutil.rmtree(self.sanctum)

    def test_a_damaged_sanctum_is_never_reborn(self):
        self.assertEqual(self.init().returncode, 0, msg="init")
        (self.sanctum / ".born").write_text("2026-07-14\n")
        (self.sanctum / "BOND.md").write_text("# Bond with Alex\n")
        (self.sanctum / "sessions" / "2026-07-14.md").write_text("first breath\n")
        (self.sanctum / "CREED.md").unlink()

        damaged = self.wake()
        self.assertEqual(damaged.returncode, 1, msg="wake on a damaged sanctum")
        self.assertIn("MODE: DAMAGED", damaged.stdout, msg="wake on a damaged sanctum")
        self.assertIn("Missing identity files: CREED.md", damaged.stdout, msg="damaged wake")
        self.assertNotIn("FIRST_BREATH", damaged.stdout, msg="wake on a damaged sanctum")

        reinit = self.init()
        self.assertEqual(reinit.returncode, 1, msg="init on a damaged sanctum")
        self.assertIn("is missing CREED.md", reinit.stderr, msg="init on a damaged sanctum")
        self.assertEqual(
            (self.sanctum / "BOND.md").read_text(), "# Bond with Alex\n", msg="BOND after init"
        )
        self.assertEqual(
            (self.sanctum / ".born").read_text(), "2026-07-14\n", msg=".born after init"
        )
        self.assertFalse((self.sanctum / "CREED.md").exists(), msg="CREED after init")

    def test_a_mistyped_home_never_starts_a_second_identity(self):
        typo = self.home.parent / f"{self.home.name}-typo"
        waking = run_script("wake.py", self.project, home=typo)
        self.assertEqual(waking.returncode, 1, msg="wake with a mistyped home")
        self.assertIn("MODE: HOME_MISCONFIGURED", waking.stdout, msg="wake with a typo")
        self.assertIn(str(typo), waking.stdout, msg="wake with a mistyped home")

        initialized = run_script("init-sanctum.py", self.project, SKILL_ROOT, home=typo)
        self.assertEqual(initialized.returncode, 2, msg="init with a mistyped home")
        self.assertFalse(typo.exists(), msg="typo home after init")

        curated = run_script("curate.py", self.project, home=typo)
        self.assertEqual(curated.returncode, 2, msg="curate with a mistyped home")


if __name__ == "__main__":
    unittest.main()
