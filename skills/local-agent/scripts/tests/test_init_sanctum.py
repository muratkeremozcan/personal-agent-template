#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for init-sanctum.py: scaffolding, substitution, capability discovery."""

import contextlib
import importlib.util
import io
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = SKILL_ROOT / "scripts" / "init-sanctum.py"

spec = importlib.util.spec_from_file_location("init_sanctum", SCRIPT_PATH)
init_sanctum = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_sanctum)

import _sanctum  # noqa: E402  importable once init-sanctum.py has put scripts/ on sys.path

BIRTH = date(2026, 8, 1)


class InitCase(unittest.TestCase):
    """Each test gets its own home, a fixed birth date, and no real archive."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        project = tempfile.TemporaryDirectory()
        self.addCleanup(project.cleanup)
        self.project = Path(project.name).resolve()
        self.sanctum = self.home / "_bmad" / "memory" / "local-agent"

        env = mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LOCAL_AGENT_ARCHIVE", None)
        clock = mock.patch.object(init_sanctum, "local_today", return_value=BIRTH)
        clock.start()
        self.addCleanup(clock.stop)

    def run_init(self, *args):
        """Run main() with captured output: (exit code, stdout, stderr)."""
        argv = list(args) if args else [str(self.project), str(SKILL_ROOT)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = init_sanctum.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()


class ScaffoldTests(InitCase):
    """One scaffold against the real skill bundle, inspected one subject per test."""

    def setUp(self):
        super().setUp()
        self.code, self.out, self.err = self.run_init()
        self.assertEqual(self.code, 0, self.err)

    def test_every_identity_file_and_structural_folder_exists(self):
        for name in _sanctum.IDENTITY_FILES:
            self.assertTrue((self.sanctum / name).is_file(), f"missing {name}")
        for folder in ("capabilities", "knowledge", "sessions", "references", "scripts"):
            self.assertTrue((self.sanctum / folder).is_dir(), f"missing {folder}/")
        self.assertIn(f"Created sanctum at {self.sanctum}", self.out)
        self.assertIn("First Breath scaffolding complete.", self.out)

    def test_templates_carry_the_birth_date_and_keep_agent_placeholders(self):
        persona = (self.sanctum / "PERSONA.md").read_text(encoding="utf-8")
        self.assertIn(f"- **Born:** {BIRTH.isoformat()}\n", persona)
        self.assertIn("{agent_name}", persona)

    def test_templates_point_at_the_final_sanctum_path(self):
        creed = (self.sanctum / "CREED.md").read_text(encoding="utf-8")
        self.assertIn(f"`{self.sanctum}/`", creed)
        self.assertNotIn(".partial-", creed)

    def test_capabilities_are_discovered_from_reference_frontmatter(self):
        capabilities = (self.sanctum / "CAPABILITIES.md").read_text(encoding="utf-8")
        self.assertIn("[REM]", capabilities)
        self.assertIn("[RCL]", capabilities)
        self.assertNotIn("External:", capabilities)
        self.assertIn("## Learned", capabilities)  # evolvable

    def test_bundle_only_files_stay_in_the_bundle(self):
        self.assertFalse((self.sanctum / "references" / "first-breath.md").exists())
        self.assertTrue((self.sanctum / "references" / "archive.md").is_file())
        self.assertTrue((self.sanctum / "scripts" / "wake.py").is_file())
        self.assertTrue((self.sanctum / "scripts" / "curate.py").is_file())
        self.assertFalse((self.sanctum / "scripts" / "init-sanctum.py").exists())
        self.assertFalse((self.sanctum / "scripts" / "install_global.py").exists())

    def test_no_staging_folder_is_left_behind(self):
        self.assertEqual(sorted(p.name for p in self.sanctum.parent.iterdir()), ["local-agent"])

    def test_second_run_before_birth_says_to_resume(self):
        memory = self.sanctum / "MEMORY.md"
        memory.write_text("owner-edited content should survive")
        code, out, _ = self.run_init()
        self.assertEqual(code, 0)
        self.assertIn("Skipping First Breath scaffolding.", out)
        self.assertIn("resume First Breath", out)
        self.assertNotIn("already been born", out)
        self.assertEqual(memory.read_text(), "owner-edited content should survive")

    def test_second_run_after_birth_says_born(self):
        (self.sanctum / ".born").write_text(BIRTH.isoformat())
        code, out, _ = self.run_init()
        self.assertEqual(code, 0)
        self.assertIn("This agent has already been born.", out)
        self.assertNotIn("resume", out)


class ConfigTests(InitCase):
    def test_reads_current_toml_config(self):
        bmad = self.home / "_bmad"
        bmad.mkdir()
        (bmad / "config.toml").write_text(
            '[core]\nuser_name = "Alex"\ncommunication_language = "English"\n'
        )
        (bmad / "config.user.toml").write_text('[core]\ncommunication_language = "Spanish"\n')
        code, _, err = self.run_init()
        self.assertEqual(code, 0, err)
        bond = (self.sanctum / "BOND.md").read_text()
        self.assertIn("- **Name:** Alex\n", bond)
        self.assertIn("Spanish", bond)

    def test_scaffold_lands_in_the_canonical_home_whatever_the_project_root(self):
        (self.home / "_bmad").mkdir()
        (self.home / "_bmad" / "config.toml").write_text('[core]\nuser_name = "Alex"\n')
        code, _, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.sanctum / "MEMORY.md").is_file())
        self.assertIn("Alex", (self.sanctum / "BOND.md").read_text(encoding="utf-8"))
        self.assertFalse((self.project / "_bmad").exists())


class FailureTests(InitCase):
    def test_usage_error_goes_to_stderr_with_exit_2(self):
        code, out, err = self.run_init(str(self.project))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage:", err)
        self.assertIn("skill_path", err)

    def test_missing_skill_bundle_is_refused_before_anything_is_created(self):
        bogus = self.project / "nonexistent-skill"
        code, out, err = self.run_init(str(self.project), str(bogus))
        self.assertEqual(code, 2)
        self.assertIn(f"{bogus} is not a local-agent skill directory", err)
        self.assertIn("references/, assets/", err)
        self.assertEqual(out, "")
        self.assertFalse((self.home / "_bmad").exists())

    def test_crash_mid_build_leaves_nothing_at_the_sanctum_path(self):
        with (
            mock.patch.object(init_sanctum, "copy_scripts", side_effect=OSError("disk full")),
            self.assertRaisesRegex(OSError, "disk full"),
        ):
            self.run_init()
        self.assertFalse(self.sanctum.exists())
        self.assertEqual(list(self.sanctum.parent.iterdir()), [])

        code, _, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertTrue((self.sanctum / "CAPABILITIES.md").is_file())

    def test_stale_staging_from_a_killed_run_is_removed(self):
        stale = self.sanctum.parent / "local-agent.partial-4242-deadbeef"
        (stale / "references").mkdir(parents=True)
        (stale / "INDEX.md").write_text("half written")
        old = time.time() - init_sanctum.STALE_STAGING_SECONDS - 60
        os.utime(stale, (old, old))
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertFalse(stale.exists())
        self.assertIn(f"Removed {stale.name}", out)
        self.assertTrue((self.sanctum / "INDEX.md").is_file())

    def test_staging_of_a_run_still_building_is_left_alone(self):
        live = self.sanctum.parent / "local-agent.partial-4243-cafebabe"
        live.mkdir(parents=True)
        code, out, err = self.run_init()
        self.assertEqual(code, 0, err)
        self.assertTrue(live.is_dir())
        self.assertNotIn("Removed", out)

    def test_dangling_symlink_at_the_sanctum_path_is_never_scaffolded_over(self):
        self.sanctum.parent.mkdir(parents=True)
        try:
            self.sanctum.symlink_to(self.home / "moved-away")
        except OSError:
            self.skipTest("symlinks unavailable")
        code, _, err = self.run_init()
        self.assertEqual(code, 1)
        self.assertIn("is missing INDEX.md", err)
        self.assertTrue(self.sanctum.is_symlink())

    def test_malformed_config_is_a_configuration_error(self):
        (self.home / "_bmad").mkdir()
        # Invalid UTF-8 fails under tomllib and under the 3.10 fallback parser alike.
        (self.home / "_bmad" / "config.toml").write_bytes(b'[core]\nuser_name = "caf\xe9"\n')
        code, _, err = self.run_init()
        self.assertEqual(code, 2)
        self.assertIn(f"cannot read {self.home / '_bmad' / 'config.toml'}", err)
        self.assertFalse(self.sanctum.exists())

    def test_damaged_sanctum_is_never_scaffolded_over(self):
        (self.sanctum / "sessions").mkdir(parents=True)
        (self.sanctum / "BOND.md").write_text("real bond")
        code, out, err = self.run_init()
        self.assertEqual(code, 1)
        self.assertIn("is missing INDEX.md, PERSONA.md, CREED.md, MEMORY.md, CAPABILITIES.md", err)
        self.assertNotIn("Skipping", out)
        self.assertEqual((self.sanctum / "BOND.md").read_text(), "real bond")
        self.assertFalse((self.sanctum / "PERSONA.md").exists())

    def test_relative_home_is_refused(self):
        os.environ["LOCAL_AGENT_HOME"] = "local-agent"
        code, _, err = self.run_init()
        self.assertEqual(code, 2)
        self.assertIn("relative path", err)

    def test_mistyped_home_is_refused_and_not_created(self):
        typo = self.home / "local-agnet"
        os.environ["LOCAL_AGENT_HOME"] = str(typo)
        code, _, err = self.run_init()
        self.assertEqual(code, 2)
        self.assertIn(f"LOCAL_AGENT_HOME is set to {typo}", err)
        self.assertFalse(typo.exists())


class CapabilityFormattingTests(unittest.TestCase):
    def test_external_capability_source_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            refs = Path(tmp)
            (refs / "ext.md").write_text(
                "---\nname: X\ndescription: does x\ncode: XX\ntype: external\n"
                "external-skill: some-skill\n---\nbody\n"
            )
            (refs / "prompt.md").write_text(
                "---\nname: Y\ndescription: does y\ncode: YY\ntype: prompt\n---\nbody\n"
            )
            caps = init_sanctum.discover_capabilities(refs, "references")
            by_code = {c["code"]: c for c in caps}
            self.assertEqual(by_code["XX"]["source"], "External: `some-skill`")
            self.assertEqual(by_code["YY"]["source"], "references/prompt.md")

            rendered = init_sanctum.generate_capabilities_md(caps, evolvable=True)
            self.assertIn("| External: `some-skill` |", rendered)
            self.assertNotIn("`External: `some-skill``", rendered)


if __name__ == "__main__":
    unittest.main()
