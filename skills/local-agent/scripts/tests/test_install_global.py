#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Tests for install_global.py: user-level discovery links for the agent and its companions."""

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "install_global.py"
SPEC = importlib.util.spec_from_file_location("install_global", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ValueError(f"cannot load {SCRIPT}")
install_global = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_global)

# Pinned here so an emptied or reordered module constant fails loudly.
EXPECTED_SKILL_ROOTS = (
    ".claude/skills",
    ".agents/skills",
    ".codex/skills",
    ".gemini/config/skills",
)
EXPECTED_AGENT_ROOTS = (".claude/agents",)


def expected_target(directory: str, relative_source: str) -> str:
    """The relative symlink a discovery directory at this depth should carry."""
    return os.path.join(*[".."] * len(Path(directory).parts), relative_source)


def write_skill(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8"
    )
    return directory


def links_under(home: Path) -> list[Path]:
    return sorted(path for path in home.rglob("*") if path.is_symlink())


def statuses(changes) -> list[tuple[Path, str]]:
    return [(change.destination, change.status) for change in changes]


class InstallGlobalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.source = write_skill(
            self.home / "agent-repo" / "skills" / "local-agent", "local-agent"
        )

    def add_agent(self, filename: str) -> Path:
        agent_root = self.source.parent.parent / "agents"
        agent_root.mkdir(parents=True, exist_ok=True)
        agent = agent_root / filename
        agent.write_text(f"---\nname: {filename[:-3]}\n---\n", encoding="utf-8")
        return agent

    def other_checkout(self) -> Path:
        return write_skill(self.home / "other-repo" / "skills" / "local-agent", "local-agent")

    def link(self, relative: str, target: str) -> Path:
        destination = self.home / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target)
        return destination

    def test_discovery_roots_are_the_documented_ones(self):
        self.assertEqual(
            tuple(str(path) for path in install_global.SKILL_DIRECTORIES), EXPECTED_SKILL_ROOTS
        )
        self.assertEqual(
            tuple(str(path) for path in install_global.AGENT_DIRECTORIES), EXPECTED_AGENT_ROOTS
        )

    def test_installs_relative_links_for_every_discovery_root(self):
        changes = install_global.install(self.source, self.home)

        self.assertEqual(
            statuses(changes),
            [(self.home / root / "local-agent", "installed") for root in EXPECTED_SKILL_ROOTS],
        )
        for root in EXPECTED_SKILL_ROOTS:
            destination = self.home / root / "local-agent"
            self.assertEqual(destination.resolve(), self.source)
            self.assertEqual(
                os.readlink(destination),
                expected_target(root, "agent-repo/skills/local-agent"),
            )

    def test_installs_companion_skills_alongside_the_agent(self):
        companion = write_skill(self.source.parent / "voice", "voice")
        (self.source.parent / "scratch").mkdir()  # no SKILL.md, so not a skill

        changes = install_global.install(self.source, self.home)

        self.assertEqual(len(changes), len(EXPECTED_SKILL_ROOTS) * 2)
        for root in EXPECTED_SKILL_ROOTS:
            self.assertEqual((self.home / root / "voice").resolve(), companion)
            self.assertFalse(os.path.lexists(self.home / root / "scratch"))

    def test_installs_subagent_definitions_as_file_links(self):
        agent = self.add_agent("reviewer.md")
        (agent.parent / "notes.txt").write_text("scratch", encoding="utf-8")

        changes = install_global.install(self.source, self.home)

        self.assertEqual(len(changes), len(EXPECTED_SKILL_ROOTS) + len(EXPECTED_AGENT_ROOTS))
        for root in EXPECTED_AGENT_ROOTS:
            destination = self.home / root / "reviewer.md"
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.resolve(), agent)
            self.assertFalse(os.path.lexists(self.home / root / "notes.txt"))

    def test_second_install_is_idempotent(self):
        write_skill(self.source.parent / "voice", "voice")
        self.add_agent("reviewer.md")
        first = install_global.install(self.source, self.home)
        self.assertEqual(len(first), len(EXPECTED_SKILL_ROOTS) * 2 + len(EXPECTED_AGENT_ROOTS))

        second = install_global.install(self.source, self.home)

        self.assertEqual(
            statuses(second), [(change.destination, "unchanged") for change in first]
        )

    def test_repoints_a_dangling_symlink_and_reports_its_old_target(self):
        old = "/nonexistent/old-checkout/local-agent"
        destination = self.link(".codex/skills/local-agent", old)

        changes = install_global.install(self.source, self.home)

        self.assertEqual(destination.resolve(), self.source)
        [change] = [change for change in changes if change.destination == destination]
        self.assertEqual(change.status, "repointed")
        self.assertEqual(change.previous, "/nonexistent/old-checkout/local-agent")
        self.assertEqual(
            install_global.describe(change),
            f"repointed: {destination} -> {os.readlink(destination)}"
            " (was -> /nonexistent/old-checkout/local-agent)",
        )

    def test_repoints_a_symlink_loop_as_dangling(self):
        destination = self.home / ".claude/skills/local-agent"
        destination.parent.mkdir(parents=True)
        destination.symlink_to(destination.name)

        changes = install_global.install(self.source, self.home)

        self.assertEqual(destination.resolve(), self.source)
        self.assertEqual(changes[0].status, "repointed")

    def test_repoints_a_link_elsewhere_in_this_checkout_without_force(self):
        old = self.source.parent / "retired"
        old.mkdir()
        destination = self.link(".claude/skills/local-agent", str(old))

        changes = install_global.install(self.source, self.home)

        self.assertEqual(destination.resolve(), self.source)
        self.assertEqual(changes[0].status, "repointed")
        self.assertEqual(changes[0].previous, str(old))

    def test_refuses_a_link_into_another_checkout_without_force(self):
        other = self.other_checkout()
        destination = self.link(".agents/skills/local-agent", str(other))

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.install(self.source, self.home)

        self.assertEqual(
            refusal.exception.conflicts,
            [
                (
                    f"{destination}: links into another checkout ({other});"
                    " rerun with --force to repoint it"
                ),
            ],
        )
        self.assertEqual(destination.resolve(), other)
        self.assertEqual(links_under(self.home), [destination])

    def test_force_repoints_a_link_into_another_checkout(self):
        other = self.other_checkout()
        destination = self.link(".agents/skills/local-agent", str(other))

        changes = install_global.install(self.source, self.home, force=True)

        self.assertEqual(destination.resolve(), self.source)
        [change] = [change for change in changes if change.destination == destination]
        self.assertEqual((change.status, change.previous), ("repointed", str(other)))

    def test_refuses_every_conflict_at_once_and_writes_nothing(self):
        (self.home / ".codex").write_text("", encoding="utf-8")
        real = self.home / ".agents/skills/local-agent"
        real.mkdir(parents=True)
        (self.home / ".gemini").symlink_to("/nonexistent/gemini")

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.install(self.source, self.home)

        self.assertEqual(
            refusal.exception.conflicts,
            [
                f"{real}: real directory; only symlinks are replaced",
                f"{self.home / '.codex'}: file where a directory is needed",
                f"{self.home / '.gemini'}: broken symlink where a directory is needed",
            ],
        )
        self.assertFalse(os.path.lexists(self.home / ".claude"))
        self.assertEqual(links_under(self.home), [self.home / ".gemini"])

    def test_refuses_a_real_file_at_a_destination(self):
        self.add_agent("reviewer.md")
        real = self.home / ".claude/agents/reviewer.md"
        real.parent.mkdir(parents=True)
        real.write_text("hand written", encoding="utf-8")

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.install(self.source, self.home)

        self.assertEqual(
            refusal.exception.conflicts, [f"{real}: real file; only symlinks are replaced"]
        )
        self.assertEqual(links_under(self.home), [])

    @unittest.skipIf(os.geteuid() == 0, "root can write a read-only directory")
    def test_refuses_an_unwritable_discovery_directory(self):
        locked = self.home / ".codex/skills"
        locked.mkdir(parents=True)
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.install(self.source, self.home)

        self.assertEqual(refusal.exception.conflicts, [f"{locked}: directory is not writable"])
        self.assertEqual(links_under(self.home), [])

    def test_refuses_before_setup_has_written_skill_md(self):
        (self.source / "SKILL.md").unlink()

        with self.assertRaisesRegex(RuntimeError, "SKILL.md is missing"):
            install_global.install(self.source, self.home)


class MainTests(unittest.TestCase):
    """main() in process, with the home directory and agent source pointed at a scratch tree."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.source = write_skill(
            self.home / "agent-repo" / "skills" / "local-agent", "local-agent"
        )
        for patcher in (
            mock.patch.dict(os.environ, {"HOME": str(self.home)}),
            mock.patch.object(install_global, "AGENT_SOURCE", self.source),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = install_global.main(list(argv))
            except SystemExit as exit_:
                code = exit_.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_help_prints_usage_and_installs_nothing(self):
        code, stdout, _ = self.run_main("--help")

        self.assertEqual(code, 0)
        self.assertIn("--dry-run", stdout)
        self.assertIn("--force", stdout)
        self.assertEqual(list(self.home.glob(".*")), [])

    def test_unknown_argument_is_a_usage_error(self):
        code, _, stderr = self.run_main("--bogus")

        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments", stderr)
        self.assertEqual(list(self.home.glob(".*")), [])

    def test_dry_run_reports_and_touches_nothing(self):
        dangling = self.home / ".codex/skills/local-agent"
        dangling.parent.mkdir(parents=True)
        dangling.symlink_to("/nonexistent/local-agent")

        code, stdout, stderr = self.run_main("--dry-run")

        self.assertEqual((code, stderr), (0, ""))
        lines = stdout.splitlines()
        self.assertEqual(len(lines), len(EXPECTED_SKILL_ROOTS))
        self.assertIn(
            f"would install: {self.home / '.claude/skills/local-agent'}"
            " -> ../../agent-repo/skills/local-agent",
            lines,
        )
        self.assertIn(
            f"would repoint: {dangling} -> ../../agent-repo/skills/local-agent"
            " (was -> /nonexistent/local-agent)",
            lines,
        )
        self.assertEqual(sorted(p.name for p in self.home.glob(".*")), [".codex"])
        self.assertEqual(os.readlink(dangling), "/nonexistent/local-agent")

    def test_install_reports_each_link(self):
        code, stdout, stderr = self.run_main()

        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(
            stdout.splitlines(),
            [
                f"installed: {self.home / root / 'local-agent'} -> "
                + expected_target(root, "agent-repo/skills/local-agent")
                for root in EXPECTED_SKILL_ROOTS
            ],
        )

        code, stdout, _ = self.run_main()

        self.assertEqual(code, 0)
        self.assertTrue(stdout.splitlines())
        self.assertTrue(all(line.startswith("unchanged: ") for line in stdout.splitlines()))

    def test_conflict_exits_2_and_lists_every_conflict(self):
        (self.home / ".codex").write_text("", encoding="utf-8")
        other = write_skill(self.home / "other-repo" / "skills" / "local-agent", "local-agent")
        foreign = self.home / ".claude/skills/local-agent"
        foreign.parent.mkdir(parents=True)
        foreign.symlink_to(other)

        code, stdout, stderr = self.run_main()

        self.assertEqual((code, stdout), (2, ""))
        self.assertEqual(
            stderr.splitlines(),
            [
                "install refused; nothing was changed:",
                (
                    f"  {foreign}: links into another checkout ({other});"
                    " rerun with --force to repoint it"
                ),
                f"  {self.home / '.codex'}: file where a directory is needed",
            ],
        )
        self.assertFalse(os.path.lexists(self.home / ".agents"))

    def test_dry_run_with_a_conflict_exits_2(self):
        (self.home / ".codex").write_text("", encoding="utf-8")

        code, stdout, stderr = self.run_main("--dry-run")

        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("file where a directory is needed", stderr)

    def test_force_repoints_another_checkout(self):
        other = write_skill(self.home / "other-repo" / "skills" / "local-agent", "local-agent")
        foreign = self.home / ".claude/skills/local-agent"
        foreign.parent.mkdir(parents=True)
        foreign.symlink_to(other)

        code, stdout, _ = self.run_main("--force")

        self.assertEqual(code, 0)
        self.assertIn(
            f"repointed: {foreign} -> ../../agent-repo/skills/local-agent (was -> {other})",
            stdout.splitlines(),
        )
        self.assertEqual(foreign.resolve(), self.source)

    def test_missing_skill_md_exits_1(self):
        (self.source / "SKILL.md").unlink()

        code, stdout, stderr = self.run_main()

        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("install failed: SKILL.md is missing", stderr)


class ScriptTests(unittest.TestCase):
    """The script as a user runs it: copied into a finished agent repo, HOME set to scratch."""

    def test_cli_installs_into_the_invoking_home_and_refuses_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            source = write_skill(home / "agent-repo" / "skills" / "local-agent", "local-agent")
            scripts = source / "scripts"
            scripts.mkdir()
            for name in ("install_global.py", "_sanctum.py"):
                shutil.copy2(SCRIPTS / name, scripts / name)
            env = dict(os.environ, HOME=str(home))

            def run(*argv: str):
                return subprocess.run(
                    [sys.executable, str(scripts / "install_global.py"), *argv],
                    env=env, capture_output=True, text=True, check=False,
                )

            help_run = run("--help")
            self.assertEqual(help_run.returncode, 0, help_run.stderr)
            self.assertIn("usage:", help_run.stdout)
            self.assertEqual(links_under(home), [])

            first = run()
            self.assertEqual(first.returncode, 0, first.stderr)
            link = home / ".claude" / "skills" / "local-agent"
            self.assertEqual(link.resolve(), source)
            self.assertIn(f"installed: {link} -> ", first.stdout)

            conflict = home / ".codex" / "skills" / "local-agent"
            conflict.unlink()
            conflict.mkdir()
            second = run()
            self.assertEqual(second.returncode, 2)
            self.assertIn(f"{conflict}: real directory", second.stderr)
            self.assertEqual(second.stdout, "")


if __name__ == "__main__":
    unittest.main()
