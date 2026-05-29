#!/usr/bin/env python3
"""invoke.py — Smoke-test the qwen3-0-6b-internal endpoint.

vLLM exposes an OpenAI-compatible API. We post a chat-completions payload via
SageMaker Runtime's InvokeEndpoint.

Run:
    .venv/bin/python invoke.py
    .venv/bin/python invoke.py "your custom prompt"
"""
import json
import sys
import time

import boto3

PROFILE = "HF-Sandbox-access-754289655784"
REGION = "us-east-1"
ENDPOINT_NAME = "qwen3-0-6b-internal"


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else (
        "Hello! In one sentence, what is Amazon SageMaker?"
    )

    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 128,
        "temperature": 0.2,
    }

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    runtime = session.client("sagemaker-runtime")

    print(f"[invoke.py] endpoint={ENDPOINT_NAME}", file=sys.stderr)
    print(f"[invoke.py] prompt:   {prompt}", file=sys.stderr)

    t0 = time.time()
    resp = runtime.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="application/json",
        Body=json.dumps(payload),
    )
    body = json.loads(resp["Body"].read())
    elapsed_ms = int((time.time() - t0) * 1000)
    print(f"[invoke.py] response in {elapsed_ms}ms", file=sys.stderr)

    print(json.dumps(body, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
