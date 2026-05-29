#!/usr/bin/env python3
"""Smoke-test invocation against the vLLM endpoint.

vLLM on SageMaker is OpenAI-compatible. The SageMaker Runtime sends bodies
through /invocations; the `route=v1/chat/completions` CustomAttribute tells
the container to dispatch to the OpenAI chat endpoint.
"""
import json
import os
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = Path(os.environ.get("ACTIONS_LOG", str(ROOT / "ACTIONS.log")))


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = f"{ts} [03-invoke.py] {msg}\n"
    with LOG_FILE.open("a") as f:
        f.write(line)
    sys.stderr.write(line)


def main() -> int:
    aws_profile = os.environ.get("AWS_PROFILE", "HF-Sandbox-access-754289655784")
    aws_region = os.environ.get("AWS_REGION")
    endpoint_name = os.environ.get("ENDPOINT_NAME", "qwen3-06b-endpoint")
    hf_model_id = os.environ.get("HF_MODEL_ID", "Qwen/Qwen3-0.6B")

    prompt = " ".join(sys.argv[1:]) or "Write a single-sentence haiku about deploying language models."

    session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    resolved_region = session.region_name
    if not resolved_region:
        log(f"FAIL no region for profile {aws_profile} and AWS_REGION not in env")
        return 4
    log(f"RESOLVED region={resolved_region} endpoint={endpoint_name}")

    client = session.client("sagemaker-runtime")

    payload = {
        "model": hf_model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 256,
        "temperature": 0.7,
        "top_p": 0.9,
    }

    log(f"INVOKE endpoint={endpoint_name} prompt_len={len(prompt)}")
    t0 = time.time()
    resp = client.invoke_endpoint(
        EndpointName=endpoint_name,
        ContentType="application/json",
        Body=json.dumps(payload),
        CustomAttributes="route=v1/chat/completions",
    )
    latency_ms = int((time.time() - t0) * 1000)
    body = resp["Body"].read().decode()
    log(f"INVOKE_OK latency_ms={latency_ms} bytes={len(body)}")

    print(f"--- prompt ---\n{prompt}\n")
    print(f"--- response ({latency_ms} ms) ---")
    try:
        parsed = json.loads(body)
        try:
            print(parsed["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            print(json.dumps(parsed, indent=2))
    except json.JSONDecodeError:
        print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
