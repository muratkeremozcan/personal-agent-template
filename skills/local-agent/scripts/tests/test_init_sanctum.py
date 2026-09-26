#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Unit tests for init-sanctum.py: scaffolding, substitution, capability discovery."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = SKILL_ROOT / "scripts" / "init-sanctum.py"

spec = importlib.util.spec_from_file_location("init_sanctum", SCRIPT_PATH)
init_sanctum = importlib.util.module_from_spec(spec)
spec.loader.exec_module(init_sanctum)


class InitSanctumTests(unittest.TestCase):
    def setUp(self):
        # init-sanctum resolves the canonical home from LOCAL_AGENT_HOME. Each test
        # points it at its own temporary directory so no test can touch a real
        # sanctum under ~/local-agent.
        fallback = tempfile.TemporaryDirectory()
        self.addCleanup(fallback.cleanup)
        self._env = mock.patch.dict(os.environ, {"LOCAL_AGENT_HOME": fallback.name})
        self._env.start()
        self.addCleanup(self._env.stop)

    def use_home(self, path: Path) -> None:
        os.environ["LOCAL_AGENT_HOME"] = str(path)

    def test_scaffolds_sanctum_against_real_skill_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            self.use_home(project_root)
            (project_root / "_bmad").mkdir()
            sys.argv = ["init-sanctum.py", str(project_root), str(SKILL_ROOT)]
            init_sanctum.main()

            sanctum = project_root / "_bmad" / "memory" / "local-agent"
            for name in [
                "INDEX.md",
                "PERSONA.md",
                "CREED.md",
                "BOND.md",
                "MEMORY.md",
                "CAPABILITIES.md",
            ]:
                self.assertTrue((sanctum / name).is_file(), f"missing {name}")
            self.assertTrue((sanctum / "knowledge").is_dir())

            persona = (sanctum / "PERSONA.md").read_text()
            self.assertNotIn("{birth_date}", persona)
            self.assertIn("{agent_name}", persona)

            capabilities = (sanctum / "CAPABILITIES.md").read_text()
            self.assertIn("[REM]", capabilities)
            self.assertIn("[RCL]", capabilities)
            self.assertNotIn("External:", capabilities)
            self.assertIn("## Learned", capabilities)  # evolvable

            self.assertFalse((sanctum / "references" / "first-breath.md").exists())
            self.assertTrue((sanctum / "scripts" / "wake.py").is_file())
            self.assertTrue((sanctum / "scripts" / "curate.py").is_file())
            self.assertFalse((sanctum / "scripts" / "init-sanctum.py").exists())
            self.assertFalse((sanctum / "scripts" / "install_global.py").exists())

    def test_reads_current_toml_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            self.use_home(project_root)
            bmad = project_root / "_bmad"
            bmad.mkdir()
            (bmad / "config.toml").write_text(
                '[core]\nuser_name = "Alex"\ncommunication_language = "English"\n'
            )
            (bmad / "config.user.toml").write_text(
                '[core]\ncommunication_language = "Spanish"\n'
            )
            sys.argv = ["init-sanctum.py", str(project_root), str(SKILL_ROOT)]
            init_sanctum.main()

            bond = (
                project_root / "_bmad" / "memory" / "local-agent" / "BOND.md"
            ).read_text()
            self.assertIn("Alex", bond)
            self.assertIn("Spanish", bond)

    def test_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            self.use_home(project_root)
            (project_root / "_bmad").mkdir()
            sys.argv = ["init-sanctum.py", str(project_root), str(SKILL_ROOT)]
            init_sanctum.main()
            marker = project_root / "_bmad" / "memory" / "local-agent" / "MEMORY.md"
            marker.write_text("owner-edited content should survive")

            with self.assertRaises(SystemExit) as ctx:
                init_sanctum.main()
            self.assertEqual(ctx.exception.code, 0)
            self.assertEqual(marker.read_text(), "owner-edited content should survive")

    def test_scaffold_lands_in_the_canonical_home_whatever_the_project_root(self):
        with (
            tempfile.TemporaryDirectory() as home_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            home, project = Path(home_dir), Path(project_dir)
            self.use_home(home)
            (home / "_bmad").mkdir()
            (home / "_bmad" / "config.toml").write_text('[core]\nuser_name = "Alex"\n')
            sys.argv = ["init-sanctum.py", str(project), str(SKILL_ROOT)]
            init_sanctum.main()

            sanctum = home / "_bmad" / "memory" / "local-agent"
            self.assertTrue((sanctum / "MEMORY.md").is_file())
            self.assertIn("Alex", (sanctum / "BOND.md").read_text(encoding="utf-8"))
            self.assertFalse((project / "_bmad").exists())

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
