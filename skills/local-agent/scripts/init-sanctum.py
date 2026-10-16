#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""
First Breath: Deterministic sanctum scaffolding.

This script runs BEFORE the conversational awakening. It creates the sanctum
folder structure, copies template files with config values substituted,
copies all capability files and their supporting references into the sanctum,
and auto-generates CAPABILITIES.md from capability prompt frontmatter.

After this script runs, the sanctum is fully self-contained: the agent does
not depend on the skill bundle location for normal operation.

The sanctum is built in a sibling staging folder and moved into place in one
rename at the end, so the sanctum path holds either nothing or a complete
scaffold. A run that fails partway leaves only a `<name>.partial-*` folder, which
the next run removes before it starts.

This initializes the agent's runtime sanctum memory. It reads supported BMad
YAML and TOML config files strictly to substitute values into the sanctum
templates. It never writes or authors config. Build-time customization remains
separate from runtime memory.

Usage:
    uv run init-sanctum.py <project-root> <skill-path>

    project-root: The project you are working in. Recorded in the templates and
                  printed back; it plays no part in where the sanctum lives.
    skill-path:   Path to the skill directory (where SKILL.md, references/, assets/ live)

Exit codes: 0 scaffolded or already present, 1 a damaged sanctum is in the way,
2 usage or configuration error.
"""

import argparse
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sanctum import (
    BORN_MARKER,
    SKILL_NAME,
    HomeMisconfigured,
    local_today,
    missing_identity_files,
    sanctum_home,
)

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None

# --- Agent-specific configuration ---

# Files that stay in the skill bundle (only used during First Breath)
SKILL_ONLY_FILES = {"first-breath.md"}

TEMPLATE_FILES = [
    "INDEX-template.md",
    "PERSONA-template.md",
    "CREED-template.md",
    "BOND-template.md",
    "MEMORY-template.md",
]

# Scripts that act on the skill bundle itself, so a sanctum copy would point at the wrong
# tree: First Breath scaffolding, and the discovery-link installer.
BUNDLE_ONLY_SCRIPTS = {"init-sanctum.py", "install_global.py"}

# Whether the owner can teach this agent new capabilities
EVOLVABLE = True

# --- End agent-specific configuration ---


def parse_yaml_config(config_path: Path) -> dict:
    """Simple YAML key-value parser. Handles top-level scalar values only."""
    config = {}
    if not config_path.exists():
        return config
    with open(config_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                value = value.strip().strip("'\"")
                if value:
                    config[key.strip()] = value
    return config


def _flatten_scalar_config(data: dict) -> dict:
    """Flatten nested config mappings by scalar key for template substitution."""
    flattened = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flattened.update(_flatten_scalar_config(value))
        elif isinstance(value, (str, int, float, bool)):
            flattened[key] = str(value)
    return flattened


def _parse_simple_toml(config_path: Path) -> dict:
    """Read basic TOML scalar assignments when tomllib is unavailable."""
    config = {}
    with open(config_path, encoding="utf-8") as config_file:
        for raw_line in config_file:
            line = raw_line.strip()
            if not line or line.startswith(("#", "[")) or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            if value:
                config[key.strip()] = value
    return config


def parse_toml_config(config_path: Path) -> dict:
    """Read BMad TOML config while retaining Python 3.10 compatibility."""
    if not config_path.exists():
        return {}

    if tomllib is None:
        return _parse_simple_toml(config_path)

    with open(config_path, "rb") as config_file:
        return _flatten_scalar_config(tomllib.load(config_file))


def parse_frontmatter(file_path: Path) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    meta = {}
    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return meta

    for line in match.group(1).strip().split("\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("'\"")
    return meta


def copy_references(source_dir: Path, dest_dir: Path) -> list[str]:
    """Copy all reference files (except skill-only files) into the sanctum."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []

    for source_file in sorted(source_dir.iterdir()):
        if source_file.name in SKILL_ONLY_FILES:
            continue
        if source_file.is_file():
            shutil.copy2(source_file, dest_dir / source_file.name)
            copied.append(source_file.name)

    return copied


def copy_scripts(source_dir: Path, dest_dir: Path) -> list[str]:
    """Copy any scripts the capabilities might use into the sanctum."""
    if not source_dir.exists():
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied = []

    for source_file in sorted(source_dir.iterdir()):
        if source_file.is_file() and source_file.name not in BUNDLE_ONLY_SCRIPTS:
            shutil.copy2(source_file, dest_dir / source_file.name)
            copied.append(source_file.name)

    return copied


def discover_capabilities(references_dir: Path, sanctum_refs_path: str) -> list[dict]:
    """Scan references/ for capability prompt files with frontmatter.

    A capability whose frontmatter carries `type: external` and an
    `external-skill` key is sourced as "External: `<skill>`". It does not use
    a local sanctum path because the installed skill owns the implementation.
    """
    capabilities = []

    for md_file in sorted(references_dir.glob("*.md")):
        if md_file.name in SKILL_ONLY_FILES:
            continue
        meta = parse_frontmatter(md_file)
        if meta.get("name") and meta.get("code"):
            if meta.get("type") == "external" and meta.get("external-skill"):
                source = f"External: `{meta['external-skill']}`"
            else:
                source = f"{sanctum_refs_path}/{md_file.name}"
            capabilities.append(
                {
                    "name": meta["name"],
                    "description": meta.get("description", ""),
                    "code": meta["code"],
                    "source": source,
                }
            )
    return capabilities


def generate_capabilities_md(capabilities: list[dict], evolvable: bool) -> str:
    """Generate CAPABILITIES.md content from discovered capabilities."""
    lines = [
        "# Capabilities",
        "",
        "## Built-in",
        "",
        "| Code | Name | Description | Source |",
        "|------|------|-------------|--------|",
    ]
    for cap in capabilities:
        source = cap["source"]
        rendered_source = source if source.startswith("External: `") else f"`{source}`"
        lines.append(
            f"| [{cap['code']}] | {cap['name']} | {cap['description']} | {rendered_source} |"
        )

    if evolvable:
        lines.extend(
            [
                "",
                "## Learned",
                "",
                "_Capabilities added by the owner over time. Prompts live in `capabilities/`._",
                "",
                "| Code | Name | Description | Source | Added |",
                "|------|------|-------------|--------|-------|",
                "",
                "## How to Add a Capability",
                "",
                'Tell me "I want you to be able to do X" and we\'ll create it together.',
                "I'll write the prompt, save it to `capabilities/`, and register it here.",
                "Next session, I'll know how.",
                "Load `references/capability-authoring.md` for the full creation framework.",
            ]
        )

    lines.extend(
        [
            "",
            "## Tools",
            "",
            (
                "Prefer crafting your own tools over depending on external ones. A script "
                "you wrote and saved is more reliable than an external API. Use the file "
                "system creatively."
            ),
            "",
            "### User-Provided Tools",
            "",
            "_MCP servers, APIs, or services the owner has made available. Document them here._",
        ]
    )

    return "\n".join(lines) + "\n"


def substitute_vars(content: str, variables: dict) -> str:
    """Replace {var_name} placeholders with values from the variables dict."""
    for key, value in variables.items():
        content = content.replace(f"{{{key}}}", value)
    return content


# A scaffold builds in well under a second, so a staging folder this old belongs
# to a run that was killed. A younger one may belong to a run still building.
STALE_STAGING_SECONDS = 600


def clean_stale_staging(sanctum: Path) -> list[str]:
    """Remove staging folders a killed run left beside the sanctum.

    A run that fails with an exception removes its own staging folder, so only a
    run stopped from outside (a kill, a power cut) leaves one. Age is what tells
    it apart from a concurrent run that is still building.
    """
    parent = sanctum.parent
    if not parent.is_dir():
        return []
    cutoff = time.time() - STALE_STAGING_SECONDS
    removed = []
    for stale in sorted(parent.glob(f"{sanctum.name}.partial-*")):
        if stale.is_dir() and stale.stat().st_mtime < cutoff:
            shutil.rmtree(stale)
            removed.append(stale.name)
    return removed


def build_sanctum(
    staging: Path, skill_path: Path, variables: dict, sanctum_refs_path: str
) -> None:
    """Write the whole scaffold into `staging`, which becomes the sanctum on success."""
    assets_dir = skill_path / "assets"
    references_dir = skill_path / "references"
    scripts_dir = skill_path / "scripts"

    for sub in ("capabilities", "knowledge", "sessions"):
        (staging / sub).mkdir()

    # Copy reference files (capabilities + techniques + guidance) into sanctum
    copied_refs = copy_references(references_dir, staging / "references")
    print(f"  Copied {len(copied_refs)} reference files to sanctum/references/")
    for name in copied_refs:
        print(f"    - {name}")

    # Copy any supporting scripts into sanctum
    copied_scripts = copy_scripts(scripts_dir, staging / "scripts")
    if copied_scripts:
        print(f"  Copied {len(copied_scripts)} scripts to sanctum/scripts/")
        for name in copied_scripts:
            print(f"    - {name}")

    # Copy and substitute template files
    for template_name in TEMPLATE_FILES:
        template_path = assets_dir / template_name
        # Remove "-template" from the output filename and uppercase it
        output_name = template_name.replace("-template", "").upper()
        # Fix extension casing: .MD -> .md
        output_name = output_name[:-3] + ".md"

        content = template_path.read_text(encoding="utf-8")
        content = substitute_vars(content, variables)

        (staging / output_name).write_text(content, encoding="utf-8")
        print(f"  Created {output_name}")

    # Auto-generate CAPABILITIES.md from references/ frontmatter
    capabilities = discover_capabilities(references_dir, sanctum_refs_path)
    capabilities_content = generate_capabilities_md(capabilities, evolvable=EVOLVABLE)
    (staging / "CAPABILITIES.md").write_text(capabilities_content, encoding="utf-8")
    print(
        f"  Created CAPABILITIES.md ({len(capabilities)} built-in capabilities discovered)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "project_root",
        help="the project you are working in (recorded in the templates only)",
    )
    parser.add_argument(
        "skill_path",
        help="the skill directory holding references/ and assets/",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    skill_path = Path(args.skill_path).resolve()

    # Validate everything the build reads before anything is created, so a wrong
    # skill path fails here with a clear message, before anything exists to clean up.
    absent = [d for d in ("references", "assets") if not (skill_path / d).is_dir()]
    if absent:
        print(
            f"error: {skill_path} is not a local-agent skill directory: "
            f"missing {', '.join(f'{d}/' for d in absent)}",
            file=sys.stderr,
        )
        return 2
    # Each template becomes an identity file, so a bundle missing one would publish
    # a sanctum that wake.py reports as DAMAGED on its very first run.
    absent = [t for t in TEMPLATE_FILES if not (skill_path / "assets" / t).is_file()]
    if absent:
        print(
            f"error: the skill bundle at {skill_path} is incomplete: missing "
            f"{', '.join(f'assets/{t}' for t in absent)}. Restore the bundle and run this again.",
            file=sys.stderr,
        )
        return 2

    # Paths. The sanctum and the config it is seeded from both live at the canonical
    # home; wake.py resolves the same function, so the two can never disagree. Building
    # them from project_root once scaffolded a sanctum that wake.py never found, and
    # First Breath started over on every activation from another directory.
    try:
        home = sanctum_home()
    except HomeMisconfigured as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    bmad_dir = home / "_bmad"
    sanctum = bmad_dir / "memory" / SKILL_NAME

    # Relative path for CAPABILITIES.md references (agent loads from within sanctum)
    sanctum_refs_path = "references"

    for name in clean_stale_staging(sanctum):
        print(f"Removed {name}, left by an earlier run that did not finish")

    # lexists, so a dangling symlink at the sanctum path counts as present and damaged.
    if os.path.lexists(sanctum):
        missing = missing_identity_files(sanctum)
        if missing:
            # The same state wake.py reports as DAMAGED. Scaffolding over it would bury
            # a real identity under seed placeholders.
            print(
                f"error: a sanctum exists at {sanctum} but is missing "
                f"{', '.join(missing)}. Restore those files from Git or a backup. If the "
                "folder is only a scaffold left by an interrupted run, with no conversation "
                "saved in it, move it aside and run this again.",
                file=sys.stderr,
            )
            return 1
        print(f"Sanctum already exists at {sanctum}. Skipping First Breath scaffolding.")
        if (sanctum / BORN_MARKER).is_file():
            print("This agent has already been born.")
        else:
            print(
                f"The scaffold is complete and {BORN_MARKER} is absent: resume First Breath "
                "from what the files already hold."
            )
        return 0

    # Load config
    config = {}
    for config_file, config_parser in [
        ("config.yaml", parse_yaml_config),
        ("config.toml", parse_toml_config),
        ("config.user.yaml", parse_yaml_config),
        ("config.user.toml", parse_toml_config),
    ]:
        config_path = bmad_dir / config_file
        try:
            config.update(config_parser(config_path))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            # tomllib.TOMLDecodeError is a ValueError.
            print(f"error: cannot read {config_path}: {exc}", file=sys.stderr)
            return 2

    # Build variable substitution map
    today = local_today().isoformat()
    variables = {
        "user_name": config.get("user_name", "friend"),
        "communication_language": config.get("communication_language", "English"),
        "birth_date": today,
        "project_root": str(project_root),
        "sanctum_path": str(sanctum),
    }

    sanctum.parent.mkdir(parents=True, exist_ok=True)
    # mkdir, so the sanctum gets the owner's usual permissions once it is renamed.
    staging = sanctum.parent / f"{sanctum.name}.partial-{os.getpid()}-{secrets.token_hex(4)}"
    staging.mkdir()
    print(f"Building sanctum in {staging}")
    try:
        build_sanctum(staging, skill_path, variables, sanctum_refs_path)
        incomplete = missing_identity_files(staging)
        if incomplete:
            shutil.rmtree(staging)
            print(
                f"error: the scaffold built from {skill_path} is missing "
                f"{', '.join(incomplete)}, so nothing was published. Restore the skill "
                "bundle and run this again.",
                file=sys.stderr,
            )
            return 2
        # One rename publishes the whole scaffold. It refuses a non-empty target, so a
        # sanctum another run finished meanwhile is never overwritten.
        os.replace(staging, sanctum)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Created sanctum at {sanctum}")

    print()
    print("First Breath scaffolding complete.")
    print("The conversational awakening can now begin.")
    print(f"Sanctum: {sanctum}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
