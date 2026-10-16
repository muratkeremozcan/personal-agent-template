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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _sanctum

SCRIPT = Path(__file__).resolve().parent.parent / "wake.py"


def run_wake(project_root, home, *, cwd=None, env_overrides=None):
    """Invoke wake.py the way an owner's agent does, against an isolated home.

    LOCAL_AGENT_HOME always points at `home` and LOCAL_AGENT_ARCHIVE is cleared, so
    the owner's real sanctum and archive can never leak into a result.
    `env_overrides` maps a variable to a value, or to None to unset it.
    """
    env = dict(os.environ)
    env.pop("LOCAL_AGENT_ARCHIVE", None)
    env["LOCAL_AGENT_HOME"] = str(home)
    for key, value in (env_overrides or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(project_root)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=cwd,
    )


def scaffold(home, *, born=True):
    sanctum = Path(home) / "_bmad" / "memory" / "local-agent"
    (sanctum / "sessions").mkdir(parents=True)
    for name in _sanctum.IDENTITY_FILES:
        (sanctum / name).write_text(f"{name} content", encoding="utf-8")
    if born:
        (sanctum / ".born").write_text("2026-07-14", encoding="utf-8")
    return sanctum


class ModeTests(unittest.TestCase):
    def test_first_breath_when_the_home_exists_and_the_sanctum_does_not(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            result = run_wake(project, home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: FIRST_BREATH", result.stdout)
            self.assertNotIn("MODE: FIRST_BREATH_RESUME", result.stdout)

    def test_resume_when_scaffolded_but_not_born(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            scaffold(home, born=False)
            result = run_wake(project, home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: FIRST_BREATH_RESUME", result.stdout)
            self.assertNotIn("MODE: WAKING", result.stdout)

    def test_waking_emits_every_identity_file_in_load_order(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            scaffold(home)
            result = run_wake(project, home)
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: WAKING", result.stdout)
            positions = [
                result.stdout.index(f"===== {name} =====\n{name} content")
                for name in _sanctum.IDENTITY_FILES
            ]
            self.assertEqual(positions, sorted(positions))
            self.assertNotIn("(missing:", result.stdout)

    def test_project_root_never_determines_sanctum_location(self):
        """Waking from an unrelated project must still find the canonical
        sanctum and must never trigger a false First Breath there."""
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as unrelated_project,
        ):
            sanctum = scaffold(home)
            result = run_wake(unrelated_project, home)
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
            sanctum = scaffold(Path(fake_home) / "local-agent")
            result = run_wake(
                project,
                fake_home,
                env_overrides={"LOCAL_AGENT_HOME": None, "HOME": fake_home},
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("MODE: WAKING", result.stdout)
            self.assertIn(f"Sanctum: {sanctum.resolve()}", result.stdout)

    def test_missing_positional_arg_errors(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage: wake.py <project-root>", result.stderr)


class DamagedSanctumTests(unittest.TestCase):
    """A sanctum missing identity files is a restore job, never a birth."""

    def test_each_missing_identity_file_reports_damage(self):
        for missing in _sanctum.IDENTITY_FILES:
            with (
                self.subTest(missing=missing),
                tempfile.TemporaryDirectory() as home,
                tempfile.TemporaryDirectory() as project,
            ):
                sanctum = scaffold(home)
                (sanctum / missing).unlink()
                result = run_wake(project, home)
                self.assertEqual(result.returncode, 1)
                self.assertIn("MODE: DAMAGED", result.stdout)
                self.assertIn(f"Missing identity files: {missing}\n", result.stdout)
                self.assertNotIn("FIRST_BREATH", result.stdout)
                self.assertNotIn("birth.", result.stdout)

    def test_born_sanctum_after_a_sync_conflict(self):
        """The reproduced case: born, BOND filled, a session log, CREED.md gone."""
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            sanctum = scaffold(home)
            (sanctum / "BOND.md").write_text("# Bond with Alex", encoding="utf-8")
            (sanctum / "sessions" / "2026-07-14.md").write_text("born", encoding="utf-8")
            (sanctum / "CREED.md").unlink()
            (sanctum / "MEMORY.md").unlink()

            result = run_wake(project, home)
            self.assertEqual(result.returncode, 1)
            self.assertIn("MODE: DAMAGED", result.stdout)
            self.assertIn("Missing identity files: CREED.md, MEMORY.md", result.stdout)
            self.assertIn(".born: present (2026-07-14)", result.stdout)
            self.assertIn("Do not run First Breath", result.stdout)
            self.assertIn(
                f"git -C {Path(home).resolve()} checkout HEAD -- "
                "_bmad/memory/local-agent/CREED.md _bmad/memory/local-agent/MEMORY.md",
                result.stdout,
            )
            self.assertNotIn("move it aside", result.stdout)
            self.assertEqual((sanctum / "BOND.md").read_text(), "# Bond with Alex")

    def test_unborn_folder_without_identity_files_reports_damage(self):
        """The folder an interrupted scaffold used to leave behind."""
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            (Path(home) / "_bmad" / "memory" / "local-agent" / "sessions").mkdir(parents=True)
            result = run_wake(project, home)
            self.assertEqual(result.returncode, 1)
            self.assertIn("MODE: DAMAGED", result.stdout)
            self.assertIn(".born: absent", result.stdout)
            self.assertIn("move it aside and run First Breath again", result.stdout)


class HomeMisconfiguredTests(unittest.TestCase):
    def test_mistyped_override_is_named_and_never_starts_a_birth(self):
        with (
            tempfile.TemporaryDirectory() as parent,
            tempfile.TemporaryDirectory() as project,
        ):
            typo = Path(parent) / "local-agnet"
            result = run_wake(project, typo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("MODE: HOME_MISCONFIGURED", result.stdout)
            self.assertIn(f"LOCAL_AGENT_HOME is set to {typo}", result.stdout)
            self.assertNotIn("FIRST_BREATH", result.stdout)
            self.assertFalse(typo.exists())

    def test_relative_override_is_refused_even_where_it_would_resolve(self):
        """Relative to cwd, every repo with a local-agent/ folder became the home."""
        with tempfile.TemporaryDirectory() as repo:
            scaffold(Path(repo) / "local-agent")
            result = run_wake(repo, "local-agent", cwd=repo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("MODE: HOME_MISCONFIGURED", result.stdout)
            self.assertIn("LOCAL_AGENT_HOME is set to 'local-agent'", result.stdout)
            self.assertIn("absolute path", result.stdout)
            self.assertNotIn("MODE: WAKING", result.stdout)


class EncodingTests(unittest.TestCase):
    def test_one_invalid_byte_still_wakes_and_names_the_file(self):
        with (
            tempfile.TemporaryDirectory() as home,
            tempfile.TemporaryDirectory() as project,
        ):
            sanctum = scaffold(home)
            (sanctum / "INDEX.md").write_bytes(b"# Index\n- caf\xe9 notes\n")
            result = run_wake(project, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("MODE: WAKING", result.stdout)
            self.assertIn("- caf� notes", result.stdout)
            self.assertIn("CREED.md content", result.stdout)
            index = sanctum.resolve() / "INDEX.md"
            self.assertIn(f"warning: {index} is not valid UTF-8", result.stderr)
            self.assertEqual(result.stderr.count("warning:"), 1)


if __name__ == "__main__":
    unittest.main()
