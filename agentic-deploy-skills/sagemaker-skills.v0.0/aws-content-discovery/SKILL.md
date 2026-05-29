---
name: aws-context-discovery
description: Discover the user's local AWS context (active profile, region, account ID, caller identity) at the start of any AWS task. Use this skill before any other AWS work — deploying to SageMaker, creating resources, calling AWS APIs, or anything that touches an AWS account. Use it especially when the user has not specified a region or profile explicitly, when they say things like "use my AWS account", "deploy to AWS", "use my profile", or when about to make any AWS CLI or SDK call. Never guess the region or account ID — always use this skill to read it from the local configuration first.
---

# AWS Context Discovery

Before doing any AWS work, you need to know which account, region, and identity you are operating as. This information lives in the user's local AWS configuration. Read it; do not guess it; do not ask the user for things their config file already answers.

## Why this matters

A common failure mode is the agent picking a default region like `us-east-1` because it appeared most often in training data, then deploying resources to a region the user does not actually use. The user's `~/.aws/config` already specifies their region. Read it.

A second failure mode is the agent trying to call `iam:CreateRole` without realizing the caller is an SSO assumed-role with no `iam:*` permissions. The caller identity tells you this up front. Read it.

## What to discover

Run these in order at the start of the AWS work, and remember the results for the rest of the session:

### 1. Active profile

Resolution order:
- `AWS_PROFILE` environment variable, if set
- Otherwise `default`

If the user mentioned a profile name in their prompt, that overrides both. If they mentioned one that does not exist in `~/.aws/config`, surface that clearly.

### 2. Region

Resolution order — stop at the first one that produces a value:
1. Region the user explicitly named in this conversation
2. `AWS_REGION` environment variable
3. `AWS_DEFAULT_REGION` environment variable
4. The `region` field on the active profile in `~/.aws/config`
5. Ask the user — but only after the first four have failed

Do **not** fall back to `us-east-1` or any other hardcoded default. If no region is configured anywhere, the right action is to ask, not to assume.

### 3. Credentials work, account ID, caller ARN

Run:

```bash
aws sts get-caller-identity --profile <profile> --region <region>
```

This call serves three purposes at once:
- Confirms the credentials are valid (if it fails, stop and surface the error — do not proceed to other work that will fail more confusingly)
- Returns the `Account` ID, which you will need for ARN construction later
- Returns the `Arn` of the caller, which tells you whether this is an IAM user, an assumed-role, or — critically — an SSO assumed-role

### 4. Identify SSO / assumed-role principals

Look at the `Arn` field from `get-caller-identity`. The patterns matter:

| ARN pattern | What it means | IAM implications |
|---|---|---|
| `arn:aws:iam::<acct>:user/<name>` | Long-lived IAM user | Probably has whatever policies are attached to the user; check before assuming |
| `arn:aws:sts::<acct>:assumed-role/AWSReservedSSO_<...>/<email>` | **SSO assumed-role** | Typically **cannot** create IAM roles, attach policies, or modify IAM resources |
| `arn:aws:sts::<acct>:assumed-role/<role-name>/<session>` | Regular assumed-role (e.g. from `aws sts assume-role`) | Permissions depend on the role; check |

If the caller is an SSO assumed-role, surface this to the user immediately, before later skills fail on `iam:CreateRole`:

> Heads up: you're authenticated via SSO (`AWSReservedSSO_HF-Sandbox-access_...`). SSO principals usually can't create IAM roles directly. If we need a SageMaker execution role, I'll first look for an existing one in the account — if none exists, you'll need to ask whoever manages your AWS access to create one (or grant you `iam:CreateRole`).

This is the single most important thing this skill does. Surfacing it now turns a confusing mid-deployment error into a five-second conversation.

## Minimal commands to run

```bash
# What profile and region will the CLI actually use?
aws configure list

# Are credentials valid? What identity?
aws sts get-caller-identity

# If the user named a specific profile:
aws sts get-caller-identity --profile <profile-name>
```

`aws configure list` is faster than parsing `~/.aws/config` yourself and shows the effective resolved values including env-var overrides. Prefer it.

If you need to read raw config (e.g. to list available profiles), `~/.aws/config` and `~/.aws/credentials` are plain INI files. Do not write to them.

## What to report back

After running discovery, briefly tell the user what you found — one or two lines, not a wall of text:

> Working with profile `dwarez-hf` in `eu-west-1`, account `754289655784`. You're authenticated via SSO, so we'll need to use an existing IAM role rather than create one.

Then proceed. Do not ask the user to confirm the region you just read from their config — they configured it; that is the confirmation.

If something is **wrong** (credentials expired, profile does not exist, no region anywhere), stop and surface the specific error before continuing. Do not paper over it with assumptions.

## What this skill does not do

- Does not install or configure the AWS CLI — assume it is present. If `aws` is not on PATH, say so and stop.
- Does not run `aws configure` interactively — that is the user's job, not yours.
- Does not write to `~/.aws/` files.
- Does not enumerate every available profile unless the user asks — just resolve the active one.
- Does not check service-specific permissions (that is `sagemaker-iam-preflight`) — only identity and basic credential validity.
