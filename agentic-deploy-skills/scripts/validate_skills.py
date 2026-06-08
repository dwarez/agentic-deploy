#!/usr/bin/env python3
"""Structural validation for the SageMaker deployment skills.

Runs every structural check we want enforced on every commit:
- SKILL.md frontmatter is valid YAML and within Codex's limits
- Python and shell scripts parse cleanly and have executable bits set
- Scripts referenced from SKILL.md actually exist on disk
- JSON reference files parse
- No junk files committed

Usage:
    python scripts/validate_skills.py              # check the default location
    python scripts/validate_skills.py path/to/dir  # check a specific directory

Exits 0 on success, 1 on any check failure. All issues are reported before
exit (so you see every problem at once, not just the first).

Designed to run in <2s locally and in CI. No network, no AWS, no LLM.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(2)


# Codex enforces this; Claude Code is more permissive but matching the stricter
# limit keeps us portable.
CODEX_DESCRIPTION_MAX_CHARS = 1024

# Skill names need to be safe identifiers: lowercase letters, digits, and
# hyphens. Matches what both Codex and Claude Code accept without quoting.
VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Frontmatter pattern: starts with --- on its own line, ends with --- on its
# own line. Re.DOTALL lets . match newlines inside the body.
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# Pattern for finding script references in SKILL.md prose. Matches two forms:
#
#   1. Same-skill:   `<skill-path>/scripts/foo.sh` or bare `scripts/foo.sh`
#                    These resolve relative to the SKILL.md's own directory.
#
#   2. Cross-skill:  `other-skill-name/scripts/foo.py`
#                    These resolve relative to the skills-root directory.
#
# Group 1 captures the optional skill-name prefix (for cross-skill refs).
# Group 2 captures the `scripts/...` portion.
#
# False negatives (missing a reference we should have caught) are better than
# false positives (flagging valid prose as broken), so the regex is deliberately
# conservative — it only matches paths that clearly look like script refs.
SCRIPT_REF_RE = re.compile(
    r"(?:<skill-path>/|([a-z][a-z0-9-]*)/)?(scripts/[A-Za-z0-9_./-]+\.(?:sh|py))"
)

# Filenames we never want to see committed.
JUNK_FILE_PATTERNS = [
    "__pycache__",
    ".DS_Store",
    ".pyc",
    ".pyo",
    "Thumbs.db",
]


@dataclass
class ValidationResult:
    """Accumulates errors and warnings across all checks."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_run: int = 0

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def is_executable(path: Path) -> bool:
    """True if the file has any executable bit set."""
    return bool(path.stat().st_mode & 0o111)


def has_shebang(path: Path) -> bool:
    """True if the file's first line starts with #!."""
    try:
        with path.open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def read_shebang(path: Path) -> str:
    """First line of the file, or '' if unreadable."""
    try:
        return path.read_text().splitlines()[0] if path.read_text() else ""
    except (OSError, UnicodeDecodeError):
        return ""


# ---------------------------------------------------------------------------
# Per-file checks
# ---------------------------------------------------------------------------
def check_skill_md(skill_md: Path, result: ValidationResult) -> dict | None:
    """Validate a SKILL.md file. Returns the parsed frontmatter on success."""
    rel = skill_md
    result.checks_run += 1

    try:
        content = skill_md.read_text()
    except OSError as e:
        result.error(f"{rel}: cannot read file: {e}")
        return None

    if not content.startswith("---\n"):
        result.error(f"{rel}: must start with '---\\n' (frontmatter delimiter)")
        return None

    m = FRONTMATTER_RE.match(content)
    if not m:
        result.error(f"{rel}: frontmatter is not properly closed with '---'")
        return None

    try:
        frontmatter = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        result.error(f"{rel}: invalid YAML in frontmatter: {e}")
        return None

    if not isinstance(frontmatter, dict):
        result.error(
            f"{rel}: frontmatter must be a YAML mapping, got {type(frontmatter).__name__}"
        )
        return None

    # Required fields
    for field_name in ("name", "description"):
        if field_name not in frontmatter:
            result.error(f"{rel}: missing required field '{field_name}'")

    # name: type, format, directory match
    name = frontmatter.get("name")
    if name is not None:
        if not isinstance(name, str):
            result.error(f"{rel}: 'name' must be a string, got {type(name).__name__}")
        elif not VALID_NAME_RE.match(name):
            result.error(
                f"{rel}: 'name' must match {VALID_NAME_RE.pattern!r}, got {name!r}"
            )
        else:
            expected = skill_md.parent.name
            if name != expected:
                result.error(
                    f"{rel}: 'name' field is {name!r} but parent directory is {expected!r} — they must match"
                )

    # description: type, length, non-empty
    description = frontmatter.get("description")
    if description is not None:
        if not isinstance(description, str):
            result.error(
                f"{rel}: 'description' must be a string, got {type(description).__name__}"
            )
        else:
            desc_len = len(description)
            if desc_len == 0:
                result.error(f"{rel}: 'description' must not be empty")
            elif desc_len > CODEX_DESCRIPTION_MAX_CHARS:
                result.error(
                    f"{rel}: 'description' is {desc_len} chars, exceeds Codex limit "
                    f"of {CODEX_DESCRIPTION_MAX_CHARS} (over by {desc_len - CODEX_DESCRIPTION_MAX_CHARS})"
                )

    return frontmatter


def check_script_references(
    skill_md: Path, skills_root: Path, result: ValidationResult
) -> None:
    """Verify scripts referenced in SKILL.md body actually exist on disk.

    Handles two forms:
        1. Same-skill: `scripts/foo.sh` or `<skill-path>/scripts/foo.sh`
           → resolved relative to skill_md.parent
        2. Cross-skill: `other-skill/scripts/foo.py`
           → resolved relative to skills_root
    """
    result.checks_run += 1
    skill_dir = skill_md.parent

    try:
        content = skill_md.read_text()
    except OSError:
        return  # Already reported by check_skill_md

    # Skip the frontmatter — we only check refs in the body
    m = FRONTMATTER_RE.match(content)
    body = content[m.end() :] if m else content

    for match in SCRIPT_REF_RE.finditer(body):
        skill_prefix = match.group(
            1
        )  # None for same-skill, sibling name for cross-skill
        script_path = match.group(2)  # "scripts/foo.sh"

        if skill_prefix:
            # Cross-skill reference like `other-skill/scripts/foo.py`
            candidate = skills_root / skill_prefix / script_path
            ref_display = f"{skill_prefix}/{script_path}"
        else:
            # Same-skill reference like `scripts/foo.sh` or `<skill-path>/scripts/foo.sh`
            candidate = skill_dir / script_path
            ref_display = script_path

        if not candidate.is_file():
            result.error(
                f"{skill_md}: references {ref_display!r} which does not exist at {candidate}"
            )


def check_python_script(path: Path, result: ValidationResult) -> None:
    """Validate a Python script: parses, has shebang, is executable.

    Files named with a leading underscore (e.g. `_common.py`) are treated as
    library modules — they're imported by other scripts rather than executed
    directly. We still check syntax but skip the shebang/executable checks.
    """
    result.checks_run += 1

    # Syntax
    try:
        ast.parse(path.read_text())
    except SyntaxError as e:
        result.error(f"{path}: Python syntax error at line {e.lineno}: {e.msg}")
        return
    except (OSError, UnicodeDecodeError) as e:
        result.error(f"{path}: cannot read file: {e}")
        return

    # Library modules (underscore-prefixed filenames) don't need shebangs or
    # the executable bit set — they're imported, not run.
    if path.name.startswith("_"):
        return

    # Shebang
    if not has_shebang(path):
        result.error(f"{path}: missing shebang line (expected #!/usr/bin/env python)")
        return

    shebang = read_shebang(path)
    if "/usr/bin/python" in shebang and "env" not in shebang:
        result.error(
            f"{path}: shebang {shebang!r} uses hardcoded /usr/bin/python — "
            f"use #!/usr/bin/env python or #!/usr/bin/env python3 for portability"
        )

    # Executable bit
    if not is_executable(path):
        result.error(f"{path}: not executable. Run: chmod +x {path}")


def check_shell_script(path: Path, result: ValidationResult) -> None:
    """Validate a shell script: parses, has bash shebang, is executable, uses set -euo pipefail."""
    result.checks_run += 1

    # Syntax (bash -n parses without executing)
    try:
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        result.error(f"{path}: cannot run bash -n: {e}")
        return

    if completed.returncode != 0:
        result.error(f"{path}: bash syntax error: {completed.stderr.strip()}")
        return

    try:
        content = path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        result.error(f"{path}: cannot read file: {e}")
        return

    lines = content.splitlines()
    if not lines:
        result.error(f"{path}: empty file")
        return

    # Shebang must be bash, not sh — we use bash-isms ([[ ]], arrays, etc.)
    shebang = lines[0]
    if not shebang.startswith("#!"):
        result.error(f"{path}: missing shebang line")
        return
    if "bash" not in shebang:
        result.error(
            f"{path}: shebang {shebang!r} is not bash. "
            f"Use #!/usr/bin/env bash — we use bash-only syntax."
        )

    # set -euo pipefail is a project convention. Look for it in the first 20
    # lines (after the shebang and any header comments).
    head = "\n".join(lines[:20])
    if "set -euo pipefail" not in head:
        result.warn(
            f"{path}: missing 'set -euo pipefail' in the first 20 lines. "
            f"This is a project convention for catching errors and unset vars."
        )

    # Executable bit
    if not is_executable(path):
        result.error(f"{path}: not executable. Run: chmod +x {path}")


def check_json_file(path: Path, result: ValidationResult) -> None:
    """Validate a JSON reference file."""
    result.checks_run += 1
    try:
        with path.open() as f:
            json.load(f)
    except json.JSONDecodeError as e:
        result.error(f"{path}: invalid JSON at line {e.lineno} col {e.colno}: {e.msg}")
    except OSError as e:
        result.error(f"{path}: cannot read file: {e}")


def check_no_junk_files(root: Path, result: ValidationResult) -> None:
    """Walk the tree and flag files matching JUNK_FILE_PATTERNS."""
    result.checks_run += 1
    for dirpath, dirnames, filenames in os.walk(root):
        # Don't descend into .git
        dirnames[:] = [d for d in dirnames if d != ".git"]

        for d in dirnames:
            if d in JUNK_FILE_PATTERNS:
                result.error(
                    f"{Path(dirpath) / d}: junk directory committed (should be gitignored)"
                )
        for f in filenames:
            for pattern in JUNK_FILE_PATTERNS:
                if pattern in f:
                    result.error(
                        f"{Path(dirpath) / f}: junk file committed (should be gitignored)"
                    )
                    break


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------
def validate_directory(root: Path) -> ValidationResult:
    """Run all checks against a skills directory.

    Expects root to be a directory containing one subdirectory per skill, each
    with a SKILL.md at its root. e.g. sagemaker-skills/sagemaker-deployment-planner/SKILL.md
    """
    result = ValidationResult()

    if not root.is_dir():
        result.error(f"{root}: not a directory")
        return result

    # Find skill directories (anything that contains a SKILL.md)
    skill_dirs = sorted(p.parent for p in root.glob("*/SKILL.md"))

    if not skill_dirs:
        result.error(
            f"{root}: no SKILL.md files found in immediate subdirectories. "
            f"Expected layout: {root}/<skill-name>/SKILL.md"
        )
        return result

    # Per-skill checks
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        check_skill_md(skill_md, result)
        check_script_references(skill_md, root, result)

        # Each skill directory should have exactly one SKILL.md
        skill_mds = list(skill_dir.glob("SKILL.md"))
        if len(skill_mds) != 1:
            result.error(
                f"{skill_dir}: expected exactly 1 SKILL.md, found {len(skill_mds)}"
            )

    # Per-file checks across the whole tree
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # Skip files we don't recognize as ours
        if path.suffix == ".py":
            check_python_script(path, result)
        elif path.suffix == ".sh":
            check_shell_script(path, result)
        elif path.suffix == ".json":
            check_json_file(path, result)

    # Tree-wide checks
    check_no_junk_files(root, result)

    return result


def main() -> int:
    # Default to the conventional location, but accept an explicit path.
    if len(sys.argv) > 2:
        sys.stderr.write(f"Usage: {sys.argv[0]} [<skills-dir>]\n")
        return 2

    if len(sys.argv) == 2:
        target = Path(sys.argv[1])
    else:
        # Walk up from this script to find sagemaker-skills/
        here = Path(__file__).resolve().parent
        for candidate in (
            here.parent / "sagemaker-skills",
            here / "sagemaker-skills",
            Path.cwd() / "sagemaker-skills",
        ):
            if candidate.is_dir():
                target = candidate
                break
        else:
            sys.stderr.write(
                "ERROR: could not auto-detect skills directory. "
                "Pass it explicitly: validate_skills.py <path>\n"
            )
            return 2

    print(f"Validating: {target}\n")
    result = validate_directory(target)

    # Report
    for w in result.warnings:
        print(f"WARN  {w}")
    for e in result.errors:
        print(f"ERROR {e}")

    print()
    print(f"Checks run: {result.checks_run}")
    print(f"Errors:     {len(result.errors)}")
    print(f"Warnings:   {len(result.warnings)}")

    if result.ok:
        print("\nOK — all checks passed.")
        return 0
    else:
        print("\nFAIL — fix the errors above and re-run.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
