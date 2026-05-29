---
name: sagemaker-iam-preflight
description: 'Ensure a usable SageMaker execution role exists before deploying or training. Use this skill whenever about to create a SageMaker endpoint, model, training job, or any resource that requires an execution role. Use it especially when the user has not provided a role ARN explicitly, when scripts are about to call `iam:CreateRole`, or when an AccessDenied error mentions an IAM action. Never blindly call `iam:CreateRole` — always check for existing roles first. This skill prevents the most common SageMaker deployment failure: trying to create IAM resources from an SSO principal that has no IAM write permissions.'
---

# SageMaker IAM Preflight

Every SageMaker resource needs an **execution role** — the IAM role SageMaker assumes to read model artifacts from S3, pull serving containers from ECR, and write logs. Most deployments fail here because the script tried to create a new role without checking if a usable one already existed, then blew up because the caller is an SSO principal.

This skill encodes the right order: discover, validate, only create if necessary.

## Order of operations

### Step 1 — Did the user provide a role?

Validate that one specifically:

```bash
bash <skill-path>/scripts/check_role.sh "<role-name-or-arn>"
```

On success it prints the ARN to stdout (exit 0). On failure it logs why on stderr. Don't try to silently fix a broken role — surface the problem.

### Step 2 — Discover existing roles

```bash
bash <skill-path>/scripts/check_role.sh
```

Lists roles matching common SageMaker patterns (`AmazonSageMaker-ExecutionRole-*`, `SageMakerExecutionRole*`, etc.), **ranks by last-used date** (most recent first), validates trust policy in that order, returns the first usable ARN. Most accounts that have used SageMaker before already have one.

Why rank by last-used: in accounts with multiple roles (auto-generated 2021 role + manual project role + etc.), the alphabetically-first one is rarely the actively-maintained one. The most-recently-used role is more likely to have current policies — including cross-account ECR pull. The script prints the ranking so you can see which got picked.

### Step 3 — Create, only if discovery found nothing

**If the user can create** (has IAM permissions):

```bash
bash <skill-path>/scripts/create_role.sh "<role-name>" "<model-bucket>"
```

Second arg scopes S3 access to a specific bucket. Omit if unknown; script warns and the user can update the policy later.

**If the user cannot create** (SSO principal — `aws-context-discovery` will have flagged this):

Stop and surface this clearly. Don't retry alternative IAM operations hoping one works:

> I can't find an existing SageMaker execution role, and you're authenticated via SSO so you can't create one directly. Please either:
>   - Ask your AWS admin for a SageMaker execution role ARN, or
>   - Have them grant your SSO permission set `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PutRolePolicy`

Specific instructions get unblocked fast; vague "permission denied" messages don't.

## What "validated" means

A role is usable when (1) it exists, (2) its trust policy allows `sagemaker.amazonaws.com` to `sts:AssumeRole` — see `references/trust-policy.json` for the canonical form.

`check_role.sh` verifies these two. It does **not** deep-check permissions because comprehensive analysis is expensive (`iam:SimulatePrincipalPolicy` per action) and most existing SageMaker roles are over-permissioned via `AmazonSageMakerFullAccess`. If you suspect a permissions issue at deploy time, the deployment error will tell you which action was denied — fix it then, not preemptively.

## Minimum permissions

`references/minimum-permissions.json` covers what SageMaker actually needs:
- `s3:GetObject` + `s3:ListBucket` on the model artifact bucket
- ECR pull permissions
- CloudWatch logs and metrics

Layered on top of `AmazonSageMakerFullAccess` (attached by `create_role.sh`). Replace `REPLACE_WITH_MODEL_BUCKET` in the template with the actual bucket name — `create_role.sh` does this automatically when given a bucket as its second argument.
