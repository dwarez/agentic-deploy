# Qwen3-0.6B SageMaker Deployment — Postmortem

**Date:** 2026-05-28
**Outcome:** Endpoint could not be brought to `InService` despite 6 attempts spanning 2 sessions and 3 hours of work, including a mid-session refresh of the deployment skills.
**Cost incurred:** None (no endpoint reached `InService`; SageMaker does not bill `Failed` endpoints).
**AWS state at end of session:** Clean. No endpoint, config, or model with `qwen3-0-6b*` names exists. Nothing is billing.

---

## TL;DR

**Six attempts, six identical failures.** Every attempt produced the same `CannotStartContainerError` after 11–13 minutes, with **zero CloudWatch logs ever created**. The signature is consistent across every variable I controlled — image, env vars, IAM role, ECR registry, and even instance family. The cause is almost certainly **account/infrastructure level**, not configuration. I could not diagnose further because the SSO role lacks read access to the diagnostic channels that would tell us why (Service Quotas, CloudTrail).

## What was tried, what failed

All attempts: model `qwen3-0-6b`, endpoint name `qwen3-0-6b-internal`, region `us-east-1`, account `754289655784`. All failures: `CannotStartContainerError` with no log group created.

| # | Image | Role | Instance | Env vars | Outcome |
|---|---|---|---|---|---|
| 1 | `…/vllm:0.21.0-…-sagemaker-v1.4` (regional, newest) | `AmazonSageMaker-ExecutionRole-…20211203T094339` | `ml.g5.xlarge` | minimal `SM_VLLM_*` | Failed @ 695s |
| 2 | `…/vllm:0.21.0-…-sagemaker-v1.3` (regional, prev. stable) | same | `ml.g5.xlarge` | same | Failed @ 786s |
| 3 | `…/hf-aws-dlcs/huggingface-vllm:0.20.0-…-amzn2023` (HF private mirror, same account) | `sagemaker-dlc-demo` | `ml.g5.xlarge` | added `SM_VLLM_HOST=0.0.0.0`, `TRUST_REMOTE_CODE=true`, `SAGEMAKER_CONTAINER_LOG_LEVEL=20`, `SAGEMAKER_REGION=us-east-1` | Failed @ 907s |
| — | *(skills updated by user — restart)* | | | | |
| 4 | `…/vllm:0.21.0-…-sagemaker-v1.3` (regional) | `sagemaker-dlc-demo` | `ml.g5.xlarge` | full required set per updated skill (`HOST`, `TRUST_REMOTE_CODE`, etc.) | Failed @ ~676s |
| 5 | `public.ecr.aws/deep-learning-containers/vllm:…sagemaker-v1.3` (ECR Public) | `sagemaker-dlc-demo` | `ml.g5.xlarge` | same as #4 | Rejected at `CreateModel`: *"Using non-ECR image without Vpc repository access mode is not supported"* — useful data point, not a real attempt. |
| 6 | `…/vllm:0.21.0-…-sagemaker-v1.3` (regional, back) | `sagemaker-dlc-demo` | **`ml.g4dn.xlarge`** | same as #4 | Failed @ 755s |

## What this rules out

- **Image content / tag.** Three different images tried, including one in the user's own ECR account. All fail identically.
- **Wrong env vars** (specifically the `SM_VLLM_HOST=0.0.0.0` hypothesis from the updated skill). Attempts 4 and 6 had every required env var the updated skill flags. Same failure.
- **Wrong IAM role.** Two roles tried, both with valid SageMaker trust policy, both producing identical failure.
- **Cross-account ECR pull authorization.** Attempt 5 proved SageMaker can't even *try* to pull from ECR Public without VPC — it rejects at `CreateModel` with a clear ValidationException. So in attempts 1, 2, 4, 6, the image WAS successfully pulled (we got to container start, then failed). And attempt 3 used a same-account image. Cross-account auth is not the issue.
- **Instance family.** `ml.g5.xlarge` and `ml.g4dn.xlarge` (different family, different capacity pool, different GPU) fail identically. So this isn't a g5-specific capacity issue.

## What remains as the leading hypothesis

**Account-level networking or service issue.** Specifically, one of:
1. **SageMaker-managed default VPC has broken egress** in this account/region. Container can't reach HuggingFace Hub to download the model → entrypoint hangs → SageMaker eventually gives up → reports as `CannotStartContainerError` with no logs (because the container never got far enough to write any).
2. **A service control policy (SCP) at the org level** is blocking some action SageMaker needs (EC2 instance launch, ENI creation, security group attachment) and the rejection isn't surfacing as a meaningful endpoint-level error.
3. **A SageMaker service issue** specific to this account — there are 25+ historical `qwen3-*` endpoints all in `Failed` state going back to January 2026. **There are zero `InService` endpoints in this account right now**, across any model. That's a strong account-level smell.

## What I could not check

The SSO role this session uses (`AWSReservedSSO_HF-Sandbox-access_…`) lacks read access to the systems that would actually answer the question:

| Channel | Permission needed | Result |
|---|---|---|
| Service Quotas | `servicequotas:ListServiceQuotas` | `AccessDenied` |
| CloudTrail (would show the EC2 RunInstances SageMaker is making) | `cloudtrail:LookupEvents` | `AccessDenied` |
| Container-level logs | (Container has to start first) | Log group never created across all attempts |

Without one of these, we're blind. The deployment script's job ends where SageMaker's job begins, and SageMaker's job is failing silently from where we sit.

## Recommendations for tomorrow

In rough order of effort vs payoff:

1. **Check the Service Quotas console manually** (or have an admin check). SageMaker → *ml.g5.xlarge for endpoint usage* and *ml.g4dn.xlarge for endpoint usage*. If either is 0, that confirms it; request an increase. If both are >0, the issue is networking/SCP.

2. **Try a completely different model size class.** Specifically, try deploying a small *non-vLLM* model — for example, a sentence-transformer via the HF Inference Toolkit container — to see if **any** SageMaker endpoint can reach `InService` in this account. If even that fails the same way, the issue is broader than anything LLM/vLLM-related.

3. **Check whether the SageMaker-managed default VPC has internet egress.** Run from any EC2 instance in the same VPC: `curl -m 5 https://huggingface.co`. If that fails, the route table or NAT is broken at the account level.

4. **Ask whoever administers this AWS account** whether anything has changed recently in: SCP attached to the OU containing this account; the SageMaker service-linked role; the default subnets' routing tables. The pattern of "everyone's endpoints fail" since January 2026 suggests something changed and was never fixed.

5. **If you have a working SageMaker account elsewhere**, run the same `deploy.py` against that — should reach `InService` immediately. That would prove the deployment code is correct and isolate the problem to this account.

## Files in working dir at end of session

| File | Purpose |
|---|---|
| `REPORT.md` | This file. |
| `deployment_plan.md` | The plan as drafted before the last attempt. |
| `deployment_actions.log` | Full timestamped trail of every action this session. |
| `deploy.py` | Final config — regional ECR `v1.3`, `sagemaker-dlc-demo` role, `ml.g4dn.xlarge`, full required env vars. Ready to re-run if quota/VPC/SCP is fixed. |
| `invoke.py` | OpenAI-compatible chat smoke test. |
| `teardown.py` | One-command cleanup. Idempotent. |
| `.venv/` | Python env (sagemaker 3.12.0, boto3 1.43.16). |
| `*.attempt1` | Archived artifacts from the first session before skills were updated. |

## Notes worth feeding back to the skill maintainers

- **`serving-image-selection`** now correctly flags `SM_VLLM_HOST=0.0.0.0` as required (good update). However, the bundled `resolve_image_uri.py` uses `aws ecr-public describe-images`, which SSO principals routinely lack permission for. The script falls back silently to a hardcoded tag instead of trying `aws ecr describe-images --registry-id 763104351884` (the regional cross-account query), which **does** work for SSO principals. Worth fixing.
- **`sagemaker-iam-preflight`'s** new "rank by last-used" is the right idea, but it ranked highly the role I had just used in a failed attempt — i.e., recency reflected my usage, not user-meaningful "this role has worked in the past." A better signal would be "last used by a non-failed SageMaker resource" but that's much harder to compute. Worth noting as a known limitation.
- **`sagemaker-production-defaults`'s** `deploy.py` does its job correctly — `CannotStartContainerError` is surfaced cleanly and resources are not orphaned. The 30-minute polling timeout was appropriate (we always got our failure inside it). No skill bug here.
- **None of the skills currently surface** "this account has zero `InService` endpoints — historical pattern suggests account-level issue, not config." That observation has to come from the agent reading the room. Maybe a skill addition: a quick "any successful endpoint in this account?" check before the first deploy retry.

---

End of report.
