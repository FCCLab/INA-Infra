---
name: build-push-app-images
description: >-
  Builds linux/amd64 application container images, pushes them to the lab
  registry 10.1.132.30:5000, and points INA-Infra defaults at the new tag.
  Use when the user asks to build, push, or retag CCTV (slicea-analyzer /
  slicea-publisher), IoT, OTT, or other applications/* images for amd64,
  or to use a newly built image on the testbed.
---

# Build / push amd64 app images

Lab registry: `10.1.132.30:5000` (insecure HTTP). Workloads pull by **tag**; bump the tag when the image contents change so nodes are not stuck on `IfNotPresent` cache.

## Checklist

```
Build/push progress:
- [ ] 1. App dir + Dockerfile identified
- [ ] 2. Tag chosen (nws-vX.Y-amd64)
- [ ] 3. docker build --platform linux/amd64
- [ ] 4. docker push REGISTRY/name:tag
- [ ] 5. Defaults updated to that image
- [ ] 6. GitOps / PL Deploy if a live server should roll
```

## Scripts (run these; do not invent ad-hoc docker)

| App | Directory | Script | Images |
|---|---|---|---|
| CCTV | `applications/cctv/` | `./build_push.sh` | `slicea-analyzer`, `slicea-publisher` |
| IoT | `applications/iot/` | `./build_push.sh` | `sliced-edge`, `sliced-client` |
| RTT probe | `applications/rtt_probe/` | `./build_push.sh` | `rtt-probe` |
| Throughput stats | `applications/throughput_statistics/` | `./build_push.sh` | `throughput-statistics` |
| Physical AI console | `applications/physical_ai/` | `./build-push-dashboard.sh` | `cosmo3-dashboard` (amd64 sidecar UI) |
| OTT / IoT control console | `applications/control_dashboard/` | `./build-push.sh` | `ina-control-dashboard` (amd64 sidecar UI) |
| Physical AI server | `applications/physical_ai/` | `./build-push-vllm.sh` | `cosmo3-vllm` (**amd64 A40 + arm64 GH200**, multi-arch tag) |

```bash
cd /home/fcp/INA-Infra/applications/physical_ai
IMAGE_TAG=nws-v0.7-amd64 ./build-push-aiperf.sh
IMAGE_TAG=nws-v0.7 ./build-push-vllm.sh   # local amd64 + gpu-gh82 arm64 + manifest
```

```bash
cd /home/fcp/INA-Infra/applications/cctv
# Analyzer only (default). Reuses apt/pip/torch cache; source/frontend edits
# rebuild the last few layers. Publisher: BUILD_PUBLISHER=1
IMAGE_TAG=nws-v0.7-amd64 PLATFORM=linux/amd64 ./build_push.sh
```

CCTV analyzer Dockerfile defaults to the **CPU** torch wheel (`TORCH_INDEX_URL=.../cpu`). Do not pass a CUDA index unless the user asked for a GPU analyzer image.

## After push — use the image

Update every fallback that still points at the old tag:

- `ina-infra/frontend/src/lib/applicationDefaults.ts`
- `ina-infra/frontend/src/api/client.ts` (TYPE defaults if duplicated)
- `ina-infra/backend/app/services/application_deploy.py` (`or "10.1.132.30:5000/..."`)
- `ina-infra/backend/app/services/profile_store.py` (new-profile defaults)

Saved profiles in SQLite keep the old `server_image` until the UI field is changed or PL Deploy is re-run with the new default. After defaults change, **PL Deploy** (GitOps) for application servers; client UEs are a direct edge apply.

Do not hand-edit `repos/*/namespaces/...` YAML; regenerate via the existing GitOps apply path.

## Conventions

- `set -euo pipefail` in any new build script
- Tag suffix **`-amd64`** for x86_64 images, **`-arm64`** (or `-arm64-cu128`) for GH200/vLLM; Physical AI also publishes a multi-arch `cosmo3-vllm:nws-vX.Y`
- `--platform linux/amd64` even on an amd64 host
- Verify: `curl -s http://10.1.132.30:5000/v2/<name>/tags/list`
