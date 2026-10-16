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


class HomeTestCase(unittest.TestCase):
    """A scratch home directory holding an agent repository at ~/agent-repo."""

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


class InstallGlobalTests(HomeTestCase):
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

    @unittest.skipIf(os.geteuid() == 0, "root can write a read-only directory")
    def test_rerun_succeeds_when_a_correct_link_sits_in_a_read_only_directory(self):
        first = install_global.install(self.source, self.home)
        locked = self.home / ".codex/skills"
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)

        second = install_global.install(self.source, self.home)

        self.assertEqual(
            statuses(second), [(change.destination, "unchanged") for change in first]
        )

    def test_refuses_before_setup_has_written_skill_md(self):
        (self.source / "SKILL.md").unlink()

        with self.assertRaisesRegex(RuntimeError, "SKILL.md is missing"):
            install_global.install(self.source, self.home)


class AliasedFolderTests(HomeTestCase):
    """Discovery folders that reach one physical folder through a symlinked parent."""

    def alias_agents_to_claude(self, target: str = ".claude") -> None:
        (self.home / ".claude").mkdir()
        (self.home / ".agents").symlink_to(target)

    def assert_one_physical_link(self) -> None:
        self.assertEqual(
            links_under(self.home),
            [
                self.home / ".agents",
                self.home / ".claude/skills/local-agent",
                self.home / ".codex/skills/local-agent",
                self.home / ".gemini/config/skills/local-agent",
            ],
        )
        for root in EXPECTED_SKILL_ROOTS:
            self.assertEqual((self.home / root / "local-agent").resolve(), self.source)

    def test_installs_one_link_and_reports_the_alias(self):
        self.alias_agents_to_claude()

        changes = install_global.install(self.source, self.home)

        claude = self.home / ".claude/skills/local-agent"
        self.assertEqual(
            statuses(changes),
            [(self.home / root / "local-agent", "installed") for root in EXPECTED_SKILL_ROOTS],
        )
        self.assertEqual(changes[1].alias_of.destination, claude)
        self.assertEqual(changes[1].target, changes[0].target)
        self.assertEqual(
            install_global.describe(changes[1]),
            f"installed: {self.home / '.agents/skills/local-agent'} -> {changes[0].target}"
            f" (same path as {claude})",
        )
        self.assert_one_physical_link()

    def test_second_run_reports_every_logical_path_unchanged(self):
        self.alias_agents_to_claude()
        first = install_global.install(self.source, self.home)

        second = install_global.install(self.source, self.home)

        self.assertEqual(
            statuses(second), [(change.destination, "unchanged") for change in first]
        )

    def test_repoints_a_dangling_link_once(self):
        self.alias_agents_to_claude()
        old = "/nonexistent/old-checkout/local-agent"
        self.link(".claude/skills/local-agent", old)

        changes = install_global.install(self.source, self.home)

        self.assertEqual(
            [(change.status, change.previous) for change in changes[:2]],
            [("repointed", old), ("repointed", old)],
        )
        self.assertIsNotNone(changes[1].alias_of)
        self.assert_one_physical_link()
        self.assertEqual(list(self.home.rglob("*install-backup*")), [])

    def test_refuses_aliases_that_would_link_to_different_sources(self):
        companion = write_skill(self.source.parent / "voice", "voice")

        def colliding_key(destination: Path):
            return str(destination.parent), destination.name.replace("voice", "local-agent")

        with (
            mock.patch.object(install_global, "_physical_key", colliding_key),
            self.assertRaises(install_global.InstallRefused) as refusal,
        ):
            install_global.install(self.source, self.home)

        self.assertEqual(
            refusal.exception.conflicts,
            [
                f"{self.home / root / 'voice'}: same path as {self.home / root / 'local-agent'},"
                f" which links to {self.source}; cannot also link to {companion}"
                for root in EXPECTED_SKILL_ROOTS
            ],
        )
        self.assertEqual(links_under(self.home), [])

    def test_case_probe_matches_the_filesystem(self):
        (self.home / "Probe").write_text("", encoding="utf-8")

        self.assertEqual(
            install_global._case_insensitive(self.home), (self.home / "pROBE").exists()
        )

    def test_case_aliased_parent_gets_one_link(self):
        (self.home / "Probe").write_text("", encoding="utf-8")
        if not (self.home / "pROBE").exists():
            self.skipTest("filesystem is case-sensitive")
        (self.home / "Probe").unlink()
        self.alias_agents_to_claude(".CLAUDE")

        changes = install_global.install(self.source, self.home)

        self.assertIsNotNone(changes[1].alias_of)
        self.assert_one_physical_link()


class ApplyTransactionTests(HomeTestCase):
    """apply() against a filesystem that changes after planning or fails mid-way."""

    def assert_no_backups(self) -> None:
        self.assertEqual(list(self.home.rglob("*install-backup*")), [])

    def test_successful_repoint_leaves_no_backup(self):
        self.link(".codex/skills/local-agent", "/nonexistent/local-agent")

        install_global.install(self.source, self.home)

        self.assert_no_backups()

    def test_keeps_a_real_file_that_replaced_the_planned_link(self):
        destination = self.link(".codex/skills/local-agent", str(self.other_checkout()))
        changes = install_global.plan(self.source, self.home, force=True)
        destination.unlink()
        destination.write_text("written after planning", encoding="utf-8")

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.apply(changes)

        self.assertEqual(
            refusal.exception.conflicts, [f"{destination}: changed after planning; left as it is"]
        )
        self.assertEqual(destination.read_text(encoding="utf-8"), "written after planning")
        self.assertEqual(links_under(self.home), [])
        self.assertFalse(os.path.lexists(self.home / ".claude"))
        self.assertFalse(os.path.lexists(self.home / ".agents"))
        self.assert_no_backups()

    def test_keeps_a_link_retargeted_after_planning(self):
        destination = self.link(".codex/skills/local-agent", "/nonexistent/first")
        changes = install_global.plan(self.source, self.home)
        destination.unlink()
        destination.symlink_to("/nonexistent/second")

        with self.assertRaises(install_global.InstallRefused):
            install_global.apply(changes)

        self.assertEqual(os.readlink(destination), "/nonexistent/second")
        self.assertEqual(links_under(self.home), [destination])
        self.assert_no_backups()

    def test_refuses_when_the_planned_link_disappears(self):
        destination = self.link(".codex/skills/local-agent", "/nonexistent/first")
        changes = install_global.plan(self.source, self.home)
        destination.unlink()

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.apply(changes)

        self.assertIn("removed after planning", refusal.exception.conflicts[0])
        self.assertEqual(links_under(self.home), [])

    def test_keeps_a_file_created_at_a_planned_new_link(self):
        changes = install_global.plan(self.source, self.home)
        destination = self.home / ".codex/skills/local-agent"
        destination.parent.mkdir(parents=True)
        destination.write_text("created after planning", encoding="utf-8")

        with self.assertRaises(install_global.InstallRefused) as refusal:
            install_global.apply(changes)

        self.assertEqual(
            refusal.exception.conflicts,
            [f"{destination}: created by something else after planning"],
        )
        self.assertEqual(destination.read_text(encoding="utf-8"), "created after planning")
        self.assertEqual(links_under(self.home), [])
        self.assertFalse(os.path.lexists(self.home / ".claude"))
        self.assertFalse(os.path.lexists(self.home / ".agents"))

    def test_failed_replacement_restores_the_old_link_and_undoes_earlier_writes(self):
        destination = self.link(".codex/skills/local-agent", "/nonexistent/old-checkout")
        original_symlink_to = Path.symlink_to

        def failing_symlink_to(path, target, target_is_directory=False):
            if path == destination:
                raise OSError(5, "simulated failure")
            return original_symlink_to(path, target, target_is_directory)

        with (
            mock.patch.object(Path, "symlink_to", failing_symlink_to),
            self.assertRaisesRegex(OSError, "simulated failure"),
        ):
            install_global.install(self.source, self.home)

        self.assertEqual(os.readlink(destination), "/nonexistent/old-checkout")
        self.assertEqual(links_under(self.home), [destination])
        self.assertFalse(os.path.lexists(self.home / ".claude"))
        self.assertFalse(os.path.lexists(self.home / ".agents"))
        self.assert_no_backups()

    def test_interrupt_mid_apply_undoes_earlier_writes(self):
        original_symlink_to = Path.symlink_to
        calls = []

        def interrupted_symlink_to(path, target, target_is_directory=False):
            calls.append(path)
            if len(calls) == 3:
                raise KeyboardInterrupt
            return original_symlink_to(path, target, target_is_directory)

        with (
            mock.patch.object(Path, "symlink_to", interrupted_symlink_to),
            self.assertRaises(KeyboardInterrupt),
        ):
            install_global.install(self.source, self.home)

        self.assertEqual(len(calls), 3)
        self.assertEqual(links_under(self.home), [])
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["agent-repo"])


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

    def test_dry_run_reports_an_aliased_folder_once_per_logical_path(self):
        (self.home / ".claude").mkdir()
        (self.home / ".agents").symlink_to(".claude")

        code, stdout, stderr = self.run_main("--dry-run")

        self.assertEqual((code, stderr), (0, ""))
        claude = self.home / ".claude/skills/local-agent"
        self.assertEqual(
            stdout.splitlines()[:2],
            [
                f"would install: {claude} -> ../../agent-repo/skills/local-agent",
                (
                    f"would install: {self.home / '.agents/skills/local-agent'}"
                    f" -> ../../agent-repo/skills/local-agent (same path as {claude})"
                ),
            ],
        )
        self.assertEqual(os.listdir(self.home / ".claude"), [])

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


class ScriptTests(HomeTestCase):
    """The script as a user runs it: copied into a finished agent repo, HOME set to scratch."""

    def setUp(self):
        super().setUp()
        self.scripts = self.source / "scripts"
        self.scripts.mkdir()
        for name in ("install_global.py", "_sanctum.py"):
            shutil.copy2(SCRIPTS / name, self.scripts / name)

    def run_script(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.scripts / "install_global.py"), *argv],
            env=dict(os.environ, HOME=str(self.home)),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_installs_into_the_invoking_home_and_refuses_conflicts(self):
        help_run = self.run_script("--help")
        self.assertEqual(help_run.returncode, 0, help_run.stderr)
        self.assertIn("usage:", help_run.stdout)
        self.assertEqual(links_under(self.home), [])

        first = self.run_script()
        self.assertEqual(first.returncode, 0, first.stderr)
        link = self.home / ".claude" / "skills" / "local-agent"
        self.assertEqual(link.resolve(), self.source)
        self.assertIn(f"installed: {link} -> ", first.stdout)

        conflict = self.home / ".codex" / "skills" / "local-agent"
        conflict.unlink()
        conflict.mkdir()
        second = self.run_script()
        self.assertEqual(second.returncode, 2)
        self.assertIn(f"{conflict}: real directory", second.stderr)
        self.assertEqual(second.stdout, "")

    def test_cli_installs_through_an_aliased_discovery_folder(self):
        (self.home / ".claude").mkdir()
        (self.home / ".agents").symlink_to(".claude")

        result = self.run_script()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), len(EXPECTED_SKILL_ROOTS))
        self.assertIn("(same path as ", result.stdout)
        for root in EXPECTED_SKILL_ROOTS:
            self.assertEqual((self.home / root / "local-agent").resolve(), self.source)


if __name__ == "__main__":
    unittest.main()
