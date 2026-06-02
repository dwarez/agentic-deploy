# Skill Validation

Structural CI for the SageMaker deployment skills. Catches issues that have actually bitten us in practice — invalid YAML frontmatter, descriptions over Codex's 1024-char limit, broken script syntax, non-executable scripts, references to missing files.

This is intentionally **structural**, not behavioral. It does not test what the skills *do*; it tests that they're well-formed enough to load and run.

## Running locally

From the project root:

```bash
pip install pyyaml
python scripts/validate_skills.py
```

The script auto-detects `sagemaker-skills/`. You can also pass an explicit path:

```bash
python scripts/validate_skills.py path/to/skills-dir
```

Exit code: `0` if all checks pass, `1` if any error, `2` for usage errors.

## What it checks

Per **SKILL.md**:
- Starts with `---\n` frontmatter delimiter
- Frontmatter is valid YAML
- Required fields present (`name`, `description`)
- `name` matches the parent directory name
- `name` matches the format `[a-z][a-z0-9-]*`
- `description` is non-empty and ≤ 1024 characters (Codex's enforced limit)
- Scripts referenced in the body (same-skill or cross-skill paths) exist on disk

Per **Python script** (`*.py`):
- Parses cleanly (`ast.parse`)
- Has a shebang
- Shebang uses `/usr/bin/env`, not hardcoded `/usr/bin/python`
- Executable bit is set

Per **shell script** (`*.sh`):
- Parses cleanly (`bash -n`)
- Has a bash shebang (not `sh` — we use bash-only syntax)
- Uses `set -euo pipefail` near the top (warning, not error)
- Executable bit is set

Per **JSON file** (in `references/`):
- Parses as valid JSON

Tree-wide:
- Each skill directory has exactly one `SKILL.md`
- No junk files committed (`__pycache__`, `.DS_Store`, `.pyc`, etc.)

## What it does NOT check

- Whether the skill's actual behavior is correct (that's eval territory)
- Whether scripts work against real AWS resources (that's integration testing)
- Whether the prose is clear or accurate (that's review)
- Whether the bundled scripts pass static analysis beyond syntax (no `ruff`, `mypy`, `shellcheck` here — those are separate concerns and can be added as additional CI steps)

## Running in CI

`.github/workflows/validate-skills.yml` runs the validator on every push and PR. The job takes ~10 seconds. No AWS credentials, no network, no LLM calls — it just reads files and runs `ast.parse` / `bash -n` / `yaml.safe_load`.

## Adding a check

The validator is a single file (`scripts/validate_skills.py`) organized into per-file check functions (`check_skill_md`, `check_python_script`, etc.). To add a new check:

1. Write a `check_*` function that takes the path and a `ValidationResult` and appends to `result.errors` or `result.warnings`.
2. Call it from `validate_directory` at the right level (per-skill, per-file by extension, or tree-wide).
3. Add a test case in the README's "What it checks" list.

Error vs. warning:
- **Error**: blocks the build. Use for anything that breaks loading the skill or runs incorrectly.
- **Warning**: informational. Use for style conventions that are nice to have but not strictly required.

## Known limitations

- **Script reference detection is regex-based and conservative.** It catches the common forms (`<skill-path>/scripts/foo.sh`, `scripts/foo.py`, `other-skill/scripts/foo.py`) but won't catch unusual ones. False negatives are acceptable; false positives are not.
- **The Codex 1024-char description limit is a hard requirement on Codex but not on Claude Code.** We enforce it to keep skills portable. If you decide to support only Claude Code in the future, this limit can be relaxed.
- **`set -euo pipefail` is a warning, not an error.** Some scripts (e.g. interactive ones) may have a legitimate reason to skip it. Promote to an error if you find this isn't the case for any of your scripts.
