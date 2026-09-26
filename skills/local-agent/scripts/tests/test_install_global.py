#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tests for install_global.py: user-level discovery links for the agent and its companions."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "install_global.py"
SPEC = importlib.util.spec_from_file_location("install_global", SCRIPT)
assert SPEC and SPEC.loader
install_global = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_global)

SKILL_DIRECTORIES = install_global.SKILL_DIRECTORIES
AGENT_DIRECTORIES = install_global.AGENT_DIRECTORIES


def expected_target(directory: Path, relative_source: str) -> str:
    """The relative symlink a discovery directory at this depth should carry."""
    return os.path.join(*[".."] * len(directory.parts), relative_source)


def write_skill(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8"
    )
    return directory


class InstallGlobalTests(unittest.TestCase):
    def make_repo(self, home: Path) -> Path:
        """An agent repository at ~/agent-repo, returning the agent's skill directory."""
        return write_skill(home / "agent-repo" / "skills" / "local-agent", "local-agent")

    def add_agent(self, source: Path, filename: str) -> Path:
        agent_root = source.parent.parent / "agents"
        agent_root.mkdir(parents=True, exist_ok=True)
        agent = agent_root / filename
        agent.write_text(f"---\nname: {filename[:-3]}\n---\n", encoding="utf-8")
        return agent

    def test_installs_relative_links_for_every_discovery_root(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)

            results = install_global.install(source, home)

            self.assertEqual(len(results), len(SKILL_DIRECTORIES))
            for (destination, status), skill_directory in zip(
                results, SKILL_DIRECTORIES, strict=True
            ):
                self.assertEqual(status, "installed")
                self.assertEqual(destination.resolve(), source.resolve())
                self.assertEqual(
                    os.readlink(destination),
                    expected_target(skill_directory, "agent-repo/skills/local-agent"),
                )

    def test_installs_companion_skills_alongside_the_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)
            companion = write_skill(source.parent / "voice", "voice")
            (source.parent / "scratch").mkdir()  # no SKILL.md, so not a skill

            results = install_global.install(source, home)

            self.assertEqual(len(results), len(SKILL_DIRECTORIES) * 2)
            for skill_directory in SKILL_DIRECTORIES:
                self.assertEqual(
                    (home / skill_directory / "voice").resolve(), companion.resolve()
                )
                self.assertFalse((home / skill_directory / "scratch").exists())

    def test_installs_subagent_definitions_as_file_links(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)
            agent = self.add_agent(source, "reviewer.md")
            (agent.parent / "notes.txt").write_text("scratch", encoding="utf-8")

            results = install_global.install(source, home)

            self.assertEqual(len(results), len(SKILL_DIRECTORIES) + len(AGENT_DIRECTORIES))
            for agent_directory in AGENT_DIRECTORIES:
                destination = home / agent_directory / "reviewer.md"
                self.assertTrue(destination.is_file())
                self.assertEqual(destination.resolve(), agent.resolve())

    def test_second_install_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)
            write_skill(source.parent / "voice", "voice")
            self.add_agent(source, "reviewer.md")
            install_global.install(source, home)

            results = install_global.install(source, home)

            self.assertTrue(all(status == "unchanged" for _, status in results))

    def test_repairs_a_stale_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)
            destination = home / ".codex/skills/local-agent"
            destination.parent.mkdir(parents=True)
            destination.symlink_to("/nonexistent/old-checkout/local-agent")

            install_global.install(source, home)

            self.assertEqual(destination.resolve(), source.resolve())

    def test_refuses_to_replace_a_real_directory_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)
            (home / ".agents/skills/local-agent").mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "non-symlink"):
                install_global.install(source, home)
            self.assertFalse((home / ".claude/skills/local-agent").exists())

    def test_refuses_before_setup_has_written_skill_md(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = home / "agent-repo" / "skills" / "local-agent"
            source.mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "SKILL.md is missing"):
                install_global.install(source, home)

    def test_cli_installs_into_the_invoking_home_and_reports_conflicts(self):
        """main() end to end, run from a copy of the scripts inside a finished agent repo."""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            source = self.make_repo(home)
            scripts = source / "scripts"
            scripts.mkdir()
            for name in ("install_global.py", "_sanctum.py"):
                shutil.copy2(SCRIPTS / name, scripts / name)
            env = dict(os.environ, HOME=str(home))

            def run():
                return subprocess.run(
                    [sys.executable, str(scripts / "install_global.py")],
                    env=env, capture_output=True, text=True, check=False,
                )

            first = run()
            self.assertEqual(first.returncode, 0, first.stderr)
            link = home / ".claude" / "skills" / "local-agent"
            self.assertEqual(link.resolve(), source.resolve())
            self.assertIn(f"installed: {link} -> ", first.stdout)

            conflict = home / ".codex" / "skills" / "local-agent"
            conflict.unlink()
            conflict.mkdir()
            second = run()
            self.assertEqual(second.returncode, 1)
            self.assertIn("non-symlink", second.stderr)
            self.assertEqual(second.stdout, "")


if __name__ == "__main__":
    unittest.main()
