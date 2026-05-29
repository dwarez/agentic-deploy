# SageMaker Deployment Plan: Qwen3 0.6B

Status: torn down.

## Goal

Deploy `Qwen/Qwen3-0.6B` from Hugging Face Hub on Amazon SageMaker for an internal application with low-to-moderate traffic and reasonably fast synchronous responses.

## Source Model

- Hugging Face model: `Qwen/Qwen3-0.6B`
- Model type: causal text-generation LLM
- License: Apache-2.0
- Serving compatibility: Qwen's model card documents vLLM serving and OpenAI-compatible chat completions.
- Context support: model card lists 32,768 tokens, but the first deployment should cap runtime context lower unless the app needs long-context calls.

Primary source checked: https://huggingface.co/Qwen/Qwen3-0.6B

## Recommended Path

Use a SageMaker real-time endpoint with the AWS vLLM Deep Learning Container.

Why:

- The app waits synchronously, so batch and async inference are the wrong shape.
- Low traffic still needs predictable latency, so an always-on real-time endpoint is safer than a cold-start path.
- vLLM is the right serving stack for Qwen3 and exposes an OpenAI-compatible API shape.
- The model is small enough that a single-GPU endpoint should be enough for the first production version.

## Initial Infrastructure Shape

Proposed defaults, subject to confirmation:

- Endpoint type: SageMaker real-time endpoint
- Container: AWS vLLM DLC, resolved at deploy time for the selected region
- Initial instance count: `1`
- Autoscaling minimum: `1`
- Autoscaling maximum: `2` to control cost for low internal traffic
- Data capture: off initially
- CloudWatch alarms: on
- Tags: include project/model/owner tags plus `CreatedBy=agentic-deploy-skills`

Instance recommendation:

- Preferred starting point: the cheapest available compatible GPU instance, checked in the selected region before deploy.
- Candidate order: `ml.g5.xlarge`, then `ml.g6.xlarge` if available and cheaper or better supported in-region.

Confirmed: use one always-on GPU instance for predictable latency, with cost kept low.

Resolved choice after preflight:

- AWS profile: `HF-Sandbox-access-754289655784`
- AWS region: `us-east-1`
- Account: `754289655784`
- Caller: SSO assumed role `AWSReservedSSO_HF-Sandbox-access_a9a3037b77bf6782`
- Execution role: `arn:aws:iam::754289655784:role/service-role/AmazonSageMaker-ExecutionRole-20211203T094339`
- Serving image: `763104351884.dkr.ecr.us-east-1.amazonaws.com/vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4`
- Inference AMI: `al2-ami-sagemaker-inference-gpu-3-1`
- Instance type: `ml.g4dn.xlarge` as the cheapest compatible GPU starting point
- Endpoint: `qwen3-0-6b-vllm-20260529-0845`
- Endpoint status: deleted and verified absent
- Autoscaling: deleted and verified absent
- CloudWatch alarms: deleted and verified absent
- Data capture: off

Teardown result:

- Deleted endpoint `qwen3-0-6b-vllm-20260529-0845`.
- Deleted endpoint config `qwen3-0-6b-vllm-20260529-0845-config`.
- Deleted SageMaker model `qwen3-0-6b-vllm-20260529-0845`.
- Deleted the three CloudWatch alarms created for this endpoint.
- Deleted the autoscaling policy and deregistered the scalable target.
- Did not delete the shared SageMaker execution role.
- Data capture was disabled, so no deployment-created capture objects were deleted.

## Runtime Configuration

Initial vLLM environment:

- `SM_VLLM_MODEL=Qwen/Qwen3-0.6B`
- `SM_VLLM_HOST=0.0.0.0`
- `SM_VLLM_TRUST_REMOTE_CODE=true`
- `SM_VLLM_DTYPE=float16`
- `SM_VLLM_GPU_MEMORY_UTILIZATION=0.90`
- `SM_VLLM_MAX_MODEL_LEN=40960`, matching the `max_position_embeddings` in the model config.
- `SM_VLLM_MAX_NUM_SEQS=4`, to keep KV-cache pressure controlled on the low-cost GPU.

If you need Qwen3 thinking-mode parsing exposed cleanly, I will add the relevant vLLM reasoning flags after confirming the client request format.

Smoke-test result:

- Default Qwen3 thinking mode responded but used the short token budget on `<think>` content.
- Request-level `chat_template_kwargs: {"enable_thinking": false}` returned a clean response: `Hello! How can I assist you today?`
- For latency-sensitive internal app calls, use `chat_template_kwargs.enable_thinking=false` unless the app explicitly wants reasoning output.

## Execution Plan After Approval

1. Discover local AWS context without guessing:
   - Run `aws configure list`
   - Run `aws sts get-caller-identity` with the resolved profile and region
   - Record profile, region, account ID, and caller ARN in the log

2. Set up an isolated Python driver environment:
   - Use the bundled setup script from the SageMaker skills
   - Verify `sagemaker`, `boto3`, `botocore`, and `awscli` versions

3. IAM preflight:
   - Check whether you provided a SageMaker execution role ARN
   - If not, search for an existing valid SageMaker execution role
   - Do not create or modify IAM roles unless explicitly approved

4. Resolve serving image:
   - Resolve the current regional AWS vLLM DLC image URI for the selected AWS region
   - Capture the required `InferenceAmiVersion` if the selected image requires it

5. Deploy:
   - Create SageMaker model, endpoint config, and endpoint
   - Wait for endpoint status
   - Configure autoscaling
   - Configure CloudWatch alarms
   - Record every AWS call and command in `sagemaker_deploy_actions.log`

6. Smoke test:
   - Invoke the endpoint with a minimal chat completion payload
   - Record latency, status, and sanitized response shape

7. Hand off:
   - Provide endpoint name, region, invocation example, log file path, and teardown command

## Safety Rules

- No billable AWS resource creation until you approve this plan.
- No IAM creation or policy edits unless you explicitly approve them.
- Data capture stays disabled unless you ask for it.
- The endpoint will be created with min capacity `1`; this avoids cold starts but means it bills while running.
- A teardown command will be provided immediately after deployment.

## Questions Before Proceeding

Answered:

- Use the AWS profile and region from local config.
- Use a cheap GPU-backed real-time endpoint.
- Internal app only needs IAM-based `InvokeEndpoint`; no VPC attachment for now.
- Use the model's max context length.
- Find the existing SageMaker execution role locally/in-account.
