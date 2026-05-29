#!/usr/bin/env python3
"""teardown.py — Delete the qwen3-0-6b-internal endpoint and associated resources.

Wraps the bundled teardown.sh. Deletes in safe order:
    alarms → autoscaling → endpoint (stops billing) → endpoint-config → model.

Idempotent. Does NOT delete the IAM role, model artifacts in S3, or SNS topic.

Run:
    .venv/bin/python teardown.py
"""
import os
import subprocess
import sys

PROFILE = "HF-Sandbox-access-754289655784"
REGION = "us-east-1"
ENDPOINT_NAME = "qwen3-0-6b-internal"
BUNDLED_SCRIPT = "/Users/dwarez/.claude/skills/sagemaker-production-defaults/scripts/teardown.sh"


def main() -> int:
    env = os.environ.copy()
    env["AWS_PROFILE"] = PROFILE
    print(f"[teardown.py] tearing down endpoint={ENDPOINT_NAME} region={REGION}", file=sys.stderr)
    return subprocess.call(
        ["bash", BUNDLED_SCRIPT, ENDPOINT_NAME, REGION],
        env=env,
    )


if __name__ == "__main__":
    sys.exit(main())
