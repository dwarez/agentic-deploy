# SageMaker Qwen3 Endpoint Handoff

## Endpoint

- Endpoint name: `qwen3-0-6b-vllm-20260529-0845`
- Region: `us-east-1`
- AWS profile used: `HF-Sandbox-access-754289655784`
- Instance type: `ml.g4dn.xlarge`
- Autoscaling: min `1`, max `2`
- Data capture: off
- Alarm notifications: CloudWatch alarms exist, but no SNS topic is attached

## Recommended Request Shape

Use OpenAI-compatible chat-completion JSON through SageMaker Runtime:

```json
{
  "model": "Qwen/Qwen3-0.6B",
  "messages": [
    {
      "role": "user",
      "content": "Say hello in one short sentence."
    }
  ],
  "max_tokens": 64,
  "temperature": 0.2,
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

`enable_thinking=false` avoids Qwen3 spending latency and output tokens on `<think>` content.

## CLI Smoke Test

```bash
AWS_PROFILE=HF-Sandbox-access-754289655784 \
AWS_REGION=us-east-1 \
AWS_DEFAULT_REGION=us-east-1 \
aws sagemaker-runtime invoke-endpoint \
  --endpoint-name qwen3-0-6b-vllm-20260529-0845 \
  --content-type application/json \
  --accept application/json \
  --cli-binary-format raw-in-base64-out \
  --body '{"model":"Qwen/Qwen3-0.6B","messages":[{"role":"user","content":"Say hello in one short sentence."}],"max_tokens":64,"temperature":0.2,"chat_template_kwargs":{"enable_thinking":false}}' \
  --region us-east-1 \
  /tmp/qwen3_smoke_response_nothink.json
```

Verified response content:

```text
Hello! How can I assist you today?
```

## Teardown

When this endpoint is no longer needed, stop billing by deleting it:

```bash
AWS_PROFILE=HF-Sandbox-access-754289655784 \
AWS_REGION=us-east-1 \
AWS_DEFAULT_REGION=us-east-1 \
bash /Users/dwarez/hf/projects/agentic-deploy/agentic-deploy-skills/sagemaker-skills/sagemaker-production-defaults/scripts/teardown.sh \
  qwen3-0-6b-vllm-20260529-0845 \
  us-east-1
```

The endpoint bills while min capacity is `1`.
