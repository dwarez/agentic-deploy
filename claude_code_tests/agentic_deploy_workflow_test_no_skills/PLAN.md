# SageMaker Deployment Plan — Qwen3-0.6B

**Status:** scripts prepared, NOT deployed. You run the deploy step manually.
**Owner:** dario.salvati@huggingface.co
**Date drafted:** 2026-05-28

---

## 1. What we're deploying

| Setting          | Value                                                |
|------------------|------------------------------------------------------|
| Model            | `Qwen/Qwen3-0.6B` (HF Hub)                           |
| Inference server | vLLM via the official AWS SageMaker DLC (`vllm:0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4`) |
| Instance type    | `ml.g5.xlarge` (1× NVIDIA A10G, 24GB)                |
| Endpoint name    | `qwen3-06b-endpoint`                                 |
| Region           | `eu-north-1` (resolved from profile `~/.aws/config`) |
| AWS profile      | `HF-Sandbox-access-754289655784` (SSO)               |
| IAM role         | `sagemaker-huggingface` (existing — resolved, not created) |
| Lifecycle        | Always-on; manual teardown via `scripts/99-teardown.sh` |

### Why these choices

- **Qwen3-0.6B over your "Qwen 3.5 0.8B"**: that exact name doesn't exist on HF Hub. 0.6B is the closest published Qwen3 size; bump to `Qwen3-1.7B` in `.env` if you want more headroom.
- **vLLM over TGI**: we tried TGI first. Even on the latest available tag (3.0.1 in eu-north-1 was too old), the deployment failed the endpoint ping health check because TGI predates Qwen3's architecture release. vLLM 0.21 supports Qwen3 natively, is OpenAI-compatible out of the box, and the SageMaker DLC ships with the `route=v1/chat/completions` CustomAttribute path baked in.
- **g5.xlarge**: cheapest single-GPU instance with enough VRAM. A 0.6B model is overkill for this GPU, but the latency win is huge versus CPU and you said "respond reasonably fast." Cost is ~$1.41/hr (~$1k/mo if left running).
- **Manual teardown**: you asked us NOT to deploy yet, so the script is just sitting there. When you do deploy, the endpoint bills 24/7 until `99-teardown.sh` is run.

### Cost guardrails

- ml.g5.xlarge on-demand: ~$1.41/hr → ~$33/day → ~$1,016/30 days.
- TGI container pull + model download on first start: ~5–8 minutes before endpoint is `InService`.
- Inference cost is bundled in the instance hourly rate; no per-token charge.
- **No** auto-scaling configured. If you want scale-to-zero, see "Tweak: scale-to-zero" below.

---

## 2. What you'll find in this repo

```
agentic-deploy/
├── PLAN.md                  # this file — tweak before running
├── ACTIONS.log              # append-only audit log (timestamps + every action)
├── README.md                # step-by-step usage
├── requirements.txt         # pip deps, installed into .venv by 00-preflight.sh
├── .env.example             # copy to .env, edit, source before running
├── .venv/                   # project-local Python env (created by 00-preflight.sh, gitignored)
├── iam/
│   └── trust-policy.json    # trust policy reference (we are NOT creating roles in this sandbox)
└── scripts/
    ├── 00-preflight.sh      # bootstraps .venv, checks creds & SSO. No AWS mutations.
    ├── 01-ensure-role.sh    # resolves the existing SageMaker role ARN. No IAM writes.
    ├── 02-deploy.py         # creates SageMaker model + endpoint-config + endpoint (run via .venv/bin/python)
    ├── 03-invoke.py         # sample invocation against the live endpoint (run via .venv/bin/python)
    └── 99-teardown.sh       # deletes endpoint, endpoint-config, model
```

---

## 3. Execution order (when YOU are ready)

```
set -a; source .env; set +a
bash scripts/00-preflight.sh                    # creates .venv + installs deps; no AWS writes
bash scripts/01-ensure-role.sh                  # resolves existing role ARN; no IAM writes either
export SAGEMAKER_ROLE_ARN=$(cat .role-arn)
.venv/bin/python scripts/02-deploy.py           # SageMaker writes ← billing starts after this
.venv/bin/python scripts/03-invoke.py           # smoke test
# ... when done ...
bash scripts/99-teardown.sh                     # stops billing
```

Python is always invoked via `.venv/bin/python` — system Python is only used by `00-preflight.sh` to bootstrap the venv. No `pip install` ever touches your system site-packages.

Every step appends to `ACTIONS.log` with a UTC timestamp and an actor tag (`[00-preflight]`, `[deploy.py]`, etc.) so you can reconstruct the sequence.

---

## 4. AWS resources that will be created

| Resource           | Name                                       | Created by             | Reversible by              |
|--------------------|--------------------------------------------|------------------------|----------------------------|
| IAM role           | `sagemaker-huggingface` (pre-existing)     | not created — resolved | n/a (leave it)             |
| SageMaker Model    | auto-named `huggingface-pytorch-tgi-…`     | `02-deploy.py`         | `99-teardown.sh`           |
| Endpoint config    | auto-named, same stem                      | `02-deploy.py`         | `99-teardown.sh`           |
| Endpoint           | `qwen3-06b-endpoint`                       | `02-deploy.py`         | `99-teardown.sh`           |

CloudWatch log groups (`/aws/sagemaker/Endpoints/qwen3-06b-endpoint`) will appear automatically; teardown does not delete them. They cost effectively nothing.

---

## 5. Things you might want to tweak before running

Edit `.env` (copy from `.env.example`) to override any of:

- `AWS_REGION` — unset by default; scripts resolve it from the profile's `region` field (`eu-north-1`). Set in `.env` to override (`eu-west-1`, `us-east-1`, etc.).
- `HF_MODEL_ID` — swap to `Qwen/Qwen3-1.7B` for better quality, or any other HF causal LM.
- `INSTANCE_TYPE` — `ml.g5.2xlarge` (more CPU/RAM, same GPU) or `ml.g4dn.xlarge` (cheaper, slower).
- `ENDPOINT_NAME` — make it unique if you'll run more than one.
- `VLLM_IMAGE_TAG` — vLLM SageMaker DLC tag. Default `0.21.0-gpu-py312-cu130-ubuntu22.04-sagemaker-v1.4` (latest as of 2026-05). Image URI is composed at deploy time as `763104351884.dkr.ecr.<region>.amazonaws.com/vllm:<tag>`.
- `INFERENCE_AMI_VERSION` — host AMI. CUDA-13 images need `al2-ami-sagemaker-inference-gpu-3-1` or newer.
- `MAX_MODEL_LEN` — vLLM `--max-model-len`. Conservative `4096` for KV-cache budgeting on a single A10G; bump if you need longer contexts and have headroom.
- `TENSOR_PARALLEL_SIZE` — keep at `1` for single-GPU instances like g5.xlarge.
- `HF_TOKEN` — only required if you switch to a gated model. Qwen3-0.6B is open.

### Tweak: scale-to-zero (optional, not configured by default)

Add to `02-deploy.py` after the deploy call:

```python
client = boto3.client("application-autoscaling", region_name=AWS_REGION)
client.register_scalable_target(
    ServiceNamespace="sagemaker",
    ResourceId=f"endpoint/{ENDPOINT_NAME}/variant/AllTraffic",
    ScalableDimension="sagemaker:variant:DesiredInstanceCount",
    MinCapacity=0, MaxCapacity=2,
)
```
Trade-off: cold start on first request after idle (~3–5 min). Bad for chat UX, fine for batch.

---

## 6. What could go wrong (known sharp edges)

- **SSO session expired** → `00-preflight.sh` will fail fast and tell you to `aws sso login --profile HF-Sandbox-access-754289655784`.
- **IAM eventual consistency** → after role create, the script waits 10s before deploy. If `02-deploy.py` still gets `AccessDenied`, re-run it; the role is there, just hadn't propagated.
- **vLLM image not pulled in eu-north-1** → if the endpoint fails with an image-pull error, the DLC tag may not be replicated to this region yet. Either bump `VLLM_IMAGE_TAG` to an older tag known to be there, or switch `AWS_REGION` to `us-east-1` / `eu-west-1`.
- **CUDA / driver mismatch** → if you see `CUDA error: no kernel image is available` in the container logs, the host AMI is older than the image needs. `INFERENCE_AMI_VERSION=al2-ami-sagemaker-inference-gpu-3-1` should be set; if a newer image tag bumps the CUDA requirement, bump this too.
- **Python 3.14 incompatibility with sagemaker v2 SDK** → preflight checks for `python3.12`/3.13/3.11 explicitly and refuses to proceed on 3.14. Fix: `brew install python@3.12`, then `rm -rf .venv && bash scripts/00-preflight.sh`.
- **g5.xlarge quota or availability in eu-north-1** → Stockholm has g5 instances but quotas may be 0 by default, and not every g5 size is offered. `00-preflight.sh` queries the quota and logs it; if 0, request an increase in Service Quotas for "ml.g5.xlarge for endpoint usage" or switch region in `.env` (e.g. `AWS_REGION=eu-west-1`).
- **Endpoint left running** → biggest gotcha. There is no auto-shutdown. Run teardown when you're done.

---

## 7. What this plan does NOT cover

- Putting the endpoint behind API Gateway or a Lambda for the internal app to call (orthogonal — the endpoint takes SigV4-signed `InvokeEndpoint` calls directly).
- VPC isolation / PrivateLink (endpoint is public to AWS callers with IAM; the internal app calls it via IAM, not over the internet in plaintext).
- Multi-region failover.
- Model versioning / blue-green endpoint updates.

Flag any of those if you want them folded in.
