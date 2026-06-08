# agentic-deploy

Agent Skills that let a coding agent (Claude Code, Codex) deploy models to **Amazon SageMaker** safely and repeatably — picking the right serving container, ensuring an execution role exists, and creating endpoints with autoscaling, alarms, and tagging on by default.

The skills wrap `boto3` and AWS's published [Deep Learning Containers catalog](https://aws.github.io/deep-learning-containers/reference/available_images/) directly. They deliberately **do not** use the SageMaker Python SDK — see `python-env-setup/SKILL.md` for why.

## Skills

Live in `agentic-deploy-skills/sagemaker-skills/`. `sagemaker-deployment-planner` is the entry point; it coordinates the rest:

| Skill | Role |
|---|---|
| `sagemaker-deployment-planner` | Entry point — asks clarifying questions, picks a pathway, coordinates the others |
| `aws-context-discovery` | Reads local AWS profile, region, account, caller identity |
| `python-env-setup` | Isolated Python env (3.10–3.12) with current `boto3`/`awscli` |
| `serving-image-selection` | Picks the serving container (vLLM, TEI, etc.) and resolves its image URI |
| `sagemaker-iam-preflight` | Finds or creates a usable SageMaker execution role |
| `sagemaker-production-defaults` | Creates the endpoint (real-time or async) with autoscaling, alarms, tagging |

Each skill is a directory with a `SKILL.md` plus bundled `scripts/` and `references/`.

## Install

Symlink the skills into your agent's skills directory (auto-detects Claude Code and Codex):

```bash
cd agentic-deploy-skills
bash install.sh                 # all detected agents; symlinks → `git pull` keeps them current
bash install.sh --claude        # Claude Code only (~/.claude/skills)
bash install.sh --copy          # standalone copies instead of symlinks
bash install.sh --uninstall     # remove what it installed
```

Idempotent, and won't clobber unrelated entries without `--force`. See `bash install.sh --help`.

## Deployment flow

```
deployment-planner → aws-context-discovery → python-env-setup
   → serving-image-selection → iam-preflight → production-defaults
```

The production deploy scripts (`deploy.py`, `deploy_async.py`) consume the values the earlier skills produce (region, image URI, role ARN) and emit a machine-readable JSON summary. Tear down with `teardown.sh <endpoint-name> <region>`.

## Validation

Structural CI checks every skill is well-formed (valid frontmatter, scripts parse, referenced files exist). Runs on Python 3.12, no AWS/network needed:

```bash
cd agentic-deploy-skills
pip install pyyaml
python scripts/validate_skills.py sagemaker-skills
```

See `agentic-deploy-skills/scripts/README.md` for what it does and doesn't cover.

## Layout

```
agentic-deploy-skills/      # the skill bundle (the deliverable)
  sagemaker-skills/         # one directory per skill
  scripts/validate_skills.py
claude_code_tests/          # eval scratch — agent runs with/without skills
codex_tests/                # eval scratch
kiro_test/                  # eval scratch
```
