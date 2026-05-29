---
name: sagemaker-deployment-planner
description: Plan and coordinate the deployment of a model to Amazon SageMaker AI. Use this skill whenever the user wants to deploy, host, serve, or expose a model on SageMaker or AWS — including phrases like "deploy a model", "put this model on SageMaker", "host this LLM on AWS", "create an endpoint", "serve my fine-tuned model", or any request that involves taking a model artifact and making it available for inference on AWS. Use this even when the user is vague about how they want to deploy (e.g. "I just want to get this running on AWS, you figure it out"). This is the entry-point skill for SageMaker deployment work — it asks the right clarifying questions, picks a deployment pathway, and coordinates the other deployment skills.
---

# SageMaker Deployment Planner

You are helping a user deploy a model to Amazon SageMaker. Most users invoking this skill do not want to think about AWS plumbing themselves — they want the model deployed, with reasonable defaults, in as few questions as possible. Your job is to ask only what you need to make good decisions, recommend a deployment pathway honestly, and then hand off to the specialized skills that actually execute the work.

## The shape of the work

A SageMaker deployment job has these phases. You do not need to walk the user through them explicitly — just keep them in your head:

1. **Discovery** — figure out what the user is deploying and what their constraints are
2. **Pathway selection** — pick between real-time endpoint, serverless inference, async inference, batch transform, or Bedrock Custom Model Import
3. **Environment & context preflight** — handled by `aws-context-discovery` and `python-env-setup`
4. **IAM preflight** — handled by `sagemaker-iam-preflight`
5. **Image selection** — handled by `serving-image-selection`
6. **Deployment with production defaults** — handled by `sagemaker-production-defaults`

Phases 3–6 are other skills. Your job in this skill is phases 1 and 2, plus knowing when to defer to the others.

## Discovery: ask only what you need

You will eventually need to know:

- **What model**: HuggingFace ID, S3 path to artifacts, or model name. If the user gestures vaguely ("the model I fine-tuned"), ask for the artifact location.
- **Traffic shape**: roughly how often will this be called? "A few requests an hour", "steady internal traffic", "spiky, sometimes hundreds per second"
- **Latency tolerance**: does the caller wait synchronously? Is this interactive, near-real-time, or can it be async?
- **Region constraint**: defer this to `aws-context-discovery` unless the user volunteers it
- **Cost sensitivity**: not always needed, but ask if the user seems budget-conscious or if their traffic pattern is ambiguous

Do **not** front-load all of these. Ask the smallest set that lets you make the next decision. A common minimal set is just: *what model, and roughly how often will it be called?* That alone is often enough to narrow the pathway to two candidates.

If the user has already told you something in their initial message, do not ask again. Read their prompt carefully before your first response.

## Pathway selection

You are choosing between five SageMaker deployment pathways. The decision is mostly driven by **traffic pattern**, **latency tolerance**, and **payload/processing time**:

| Pathway | When it fits | When it does not |
|---|---|---|
| **Real-time endpoint** | Steady or predictable traffic, sub-second to few-second latency, always-on availability needed | Traffic is very spiky or very sparse (wastes money on idle compute) |
| **Serverless inference** | Spiky or intermittent traffic, latency tolerance includes occasional cold starts (~10s+), simpler models | LLMs above a few B parameters (memory/cold-start limits), strict latency SLAs |
| **Async inference** | Long inference times (>60s), large payloads, or queue-friendly workloads | Interactive user-facing calls where the caller waits synchronously |
| **Batch transform** | Offline scoring over a dataset, no live endpoint needed | Anything online or interactive |
| **Bedrock Custom Model Import** | The user wants a Bedrock-compatible API, the base model family is supported, fine-tuned weights only (no custom code) | Custom inference logic, unsupported architectures, or models requiring specific serving stacks |

For LLM deployments specifically, **real-time endpoints are the default** unless the user explicitly says the traffic is spiky/sparse or the inference is long-running. Serverless looks attractive for "low traffic" cases but most LLMs exceed its memory limits or have cold-start latency that surprises users.

Present the recommendation honestly. If two pathways are both reasonable, say so and explain the tradeoff in one sentence each, then pick one. Do not bury the recommendation in a wall of options.

## Confirming the plan

Once you have enough info to make a recommendation, state it plainly:

> Based on what you've told me, I'd recommend a real-time endpoint on `ml.g5.xlarge`. The model is small enough that this is cost-effective, and your traffic pattern is steady enough that you won't be paying for idle. Alternative: serverless inference would be cheaper if your traffic dries up for hours at a time, but Qwen3-0.6B is right at the edge of serverless memory limits and cold starts would be 15–30s. Want me to proceed with the real-time endpoint?

Then wait for confirmation before kicking off the execution skills. The user should know what they're about to spend money on before you create anything.

Do **not** generate a `plan.yaml` file or any other planning artifact unless the user explicitly asks for one. The plan lives in the conversation.

## Handoff to other skills

Once the plan is confirmed, the execution flow is:

1. **`aws-context-discovery`** — runs first, every time. Reads `~/.aws/config`, identifies profile/region/account, confirms credentials work. Do not guess the region; this skill will find it.
2. **`python-env-setup`** — if any Python execution will be needed (it usually will be). Ensures isolated env, correct Python version, current SDK versions.
3. **`sagemaker-iam-preflight`** — checks for an existing execution role, validates permissions, surfaces SSO/permission limits early rather than failing on `CreateRole`.
4. **`serving-image-selection`** — picks the right serving container for the model. This is where vLLM vs JumpStart vs other choices happen.
5. **`sagemaker-production-defaults`** — generates the actual deployment code with autoscaling, alarms, data capture, and tagging baked in.

You do not need to invoke these by name to the user. Just proceed through the work; the skills activate when their patterns match. If you are about to do something one of these skills covers and the skill has not loaded, prompt yourself to consult it first.

## Style and pacing

- Real users invoking this skill are often deferring to the agent precisely because they do not want to do AWS plumbing. Match that energy. Be efficient, not exhaustive.
- One round of clarifying questions is usually enough. Two if the answer to the first round genuinely opens new questions. Three rounds and you are interrogating the user.
- When you do not know something specific (current image URI, current SDK API surface, region quotas), check it rather than guessing. The other skills handle the "how to check" details.
- If the user pushes back on a recommendation, accept it and adjust. They know their constraints better than you do.

## What this skill does not do

- Does not create files, plans, or logs as artifacts (no `plan.yaml`, no `actions.log`)
- Does not perform local smoke tests of containers before deployment
- Does not pin Python or SDK versions defensively — newer is generally better; let `python-env-setup` handle environment correctness
- Does not write the deployment code itself — that is `sagemaker-production-defaults`
