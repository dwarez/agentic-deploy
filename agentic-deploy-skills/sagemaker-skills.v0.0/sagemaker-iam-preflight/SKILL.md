---
name: sagemaker-iam-preflight
description: Ensure a usable SageMaker execution role exists before deploying or training. Use this skill whenever about to create a SageMaker endpoint, model, training job, or any resource that requires an execution role. Use it especially when the user has not provided a role ARN explicitly, when scripts are about to call `iam:CreateRole`, or when an AccessDenied error mentions an IAM action. Never blindly call `iam:CreateRole` — always check for existing roles first. This skill prevents the most common SageMaker deployment failure: trying to create IAM resources from an SSO principal that has no IAM write permissions.
---

# SageMaker IAM Preflight

Every SageMaker resource (endpoint, model, training job) needs an **execution role** — the IAM role SageMaker assumes to access your model artifacts in S3, pull serving containers from ECR, and write logs. Most deployments fail here for one of two reasons:

1. The script tried to create a new role without checking if a usable one already existed, then blew up on `iam:CreateRole` because the caller is an SSO principal.
2. The script picked up a role but the trust policy didn't allow `sagemaker.amazonaws.com` to assume it, so the deployment failed mid-flight with a confusing error.

This skill encodes the right order of operations: discover, validate, only create if necessary.

## Order of operations

Always run these steps in order. Do not skip ahead.

### Step 1 — Has the user given you a role?

If the user mentioned a role name or ARN in the conversation, validate that one specifically:

```bash
bash <skill-path>/scripts/check_role.sh "AmazonSageMaker-ExecutionRole-20240101T000000"
# or
bash <skill-path>/scripts/check_role.sh "arn:aws:iam::123456789012:role/MyRole"
```

If valid, the script prints the ARN to stdout and exits 0. Use that ARN; you're done.

If invalid, the script tells you why on stderr (doesn't exist, wrong trust policy, etc.). Do not try to silently fix it — surface the problem to the user.

### Step 2 — Discover existing roles

If the user did not name a role, search for one:

```bash
bash <skill-path>/scripts/check_role.sh
```

This lists roles matching common SageMaker patterns (`AmazonSageMaker-ExecutionRole-*`, `SageMakerExecutionRole*`, etc.), validates each one's trust policy, and returns the first usable ARN. Most AWS accounts that have used SageMaker before already have one of these — you do not need to create anything.

### Step 3 — Only if Step 2 found nothing: consider creation

If `check_role.sh` exits non-zero with "no usable role found", you have two choices:

**a) The user can create the role** (they have IAM permissions):

```bash
bash <skill-path>/scripts/create_role.sh "SageMakerExecutionRole-Project" "my-model-bucket"
```

The second argument scopes S3 access to a specific bucket. If you don't know the bucket yet, omit it — the script will warn and the user can update the policy later.

**b) The user cannot create the role** (SSO principal, restricted permissions):

Stop and surface this clearly. Do not retry. Do not try alternative IAM operations hoping one will work. Tell the user:

> I can't find an existing SageMaker execution role in this account, and you're authenticated via SSO so you can't create one directly. To proceed, please either:
>   - Ask your AWS admin to create a SageMaker execution role and give you the ARN, or
>   - Have your admin grant your SSO permission set `iam:CreateRole`, `iam:AttachRolePolicy`, and `iam:PutRolePolicy`

This wording matters. Vague messages like "permission denied, please check your access" lead to thrashing. Specific instructions get unblocked fast.

## Recognizing SSO principals early

The `aws-context-discovery` skill should have already surfaced this, but as a backup: if the caller ARN matches `arn:aws:sts::*:assumed-role/AWSReservedSSO_*`, assume IAM write permissions are unavailable until proven otherwise. The discovery path (Step 2) usually still works — `iam:ListRoles` and `iam:GetRole` are often available to SSO principals even when `iam:CreateRole` is not.

## What "validated" means

A role is **usable as a SageMaker execution role** when:

1. It exists (you can `iam:GetRole` on it)
2. Its trust policy allows `sagemaker.amazonaws.com` to call `sts:AssumeRole` — see `references/trust-policy.json` for the canonical form
3. It has permissions to access the model artifacts in S3, pull from ECR, and write CloudWatch logs

The `check_role.sh` script verifies (1) and (2). It does **not** deep-check (3), because comprehensive permission analysis is expensive (potentially many `iam:SimulatePrincipalPolicy` calls) and most existing SageMaker roles are over-permissioned via `AmazonSageMakerFullAccess` anyway. If you suspect a permissions issue at deploy time, the deployment error message will tell you exactly which action was denied — fix it then, not preemptively.

## The minimum permissions

When creating a new role, the bundled `references/minimum-permissions.json` covers what SageMaker actually needs for deployment:

- `s3:GetObject` and `s3:ListBucket` on the model artifact bucket
- ECR pull permissions (for serving container images)
- CloudWatch logs and metrics (for inference logging)

This is layered on top of `AmazonSageMakerFullAccess` (attached by `create_role.sh`). The managed policy is broad; the inline policy adds the bucket-specific S3 access that AWS managed policies don't cover.

Replace `REPLACE_WITH_MODEL_BUCKET` in the template with the actual bucket name. The `create_role.sh` script does this automatically when given a bucket as its second argument.

## What this skill does not do

- Does not create roles speculatively. Existing role first; creation only as fallback.
- Does not attempt to widen permissions on an existing role to make it work. If a role is missing something, surface that to the user — don't silently `iam:AttachRolePolicy` to a role you didn't create.
- Does not delete or modify roles. Even cleanup is the user's call.
- Does not check region-specific service quotas — that's a deployment-time concern handled elsewhere.
- Does not handle cross-account roles. If the user mentions cross-account access, stop and ask for specifics rather than guessing at trust policy modifications.
