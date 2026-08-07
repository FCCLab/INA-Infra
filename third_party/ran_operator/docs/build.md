# Build and push

Build the operator image from the submodule and push to the lab registry (`10.1.132.30:5000`). Registry trust: [`utils/registry/registry.md`](../../../utils/registry/registry.md).

## Prerequisites

```bash
git submodule update --init third_party/INA-Infra-ran-oai-operators
# Docker (or compatible) on the build host; push helper needs registry access
```

## Lab helper (preferred)

```bash
./third_party/ran_operator/scripts/build_ran_operator_image.sh
```

Defaults:

| Env | Default |
|-----|---------|
| `RAN_CONTROLLER_SRC` | `third_party/INA-Infra-ran-oai-operators` |
| `REGISTRY` | `10.1.132.30:5000` |
| `OAI_RAN_CONTROLLER_IMAGE_NAME` | `oai-ran-controller` |
| `OAI_RAN_CONTROLLER_TAG` | `latest` |
| `OAI_RAN_CONTROLLER_PLATFORM` | `linux/amd64` |

Resulting image: **`10.1.132.30:5000/oai-ran-controller:latest`**.

Point GitOps renders at it:

```bash
export OAI_RAN_OPERATOR_IMAGE=10.1.132.30:5000/oai-ran-controller:latest
./scripts/render_oai_ran_gitops.sh          # CU-CP
./scripts/render_oai_ran_du_gitops.sh       # DU
./scripts/render_oai_ran_cuup_gitops.sh     # CU-UP
# or slice stack:
./scripts/render_oai_slice_deployment_gitops.sh
./bringup/03_push_to_git_repos/push_git_repos.sh
```

## Manual build (submodule)

```bash
cd third_party/INA-Infra-ran-oai-operators
docker build --platform linux/amd64 -t oai-ran-controller:latest .
# then tag/push via scripts/push-image-to-registry.sh
```

Dockerfile: multi-stage Go **1.25.6** → distroless `nonroot`, entrypoint `/manager`.

## Develop / test in the submodule

```bash
cd third_party/INA-Infra-ran-oai-operators
make fmt vet
make test          # unit tests under internal/controller
make lint          # golangci-lint (container if Docker/Podman present)
make manifests generate
```

Module path: `workload.nephio.org/ran_deployment`.
