# OAI RAN controller docs

Kubernetes operator for OpenAirInterface RAN NFs (**CU-CP**, **CU-UP**, **DU**). Source lives in the Git submodule [`third_party/ina-infra-oai-ran-controller`](../../ina-infra-oai-ran-controller/) ([FCCLab/ina-infra-oai-ran-controller](https://github.com/FCCLab/ina-infra-oai-ran-controller)), a fork of the Nephio OAI RAN operator.

| Doc | Contents |
|-----|----------|
| [architecture.md](architecture.md) | Reconcile loop, providers, created resources, Multus |
| [api.md](api.md) | `NFDeployment` + config CRs (`PLMN`, `RANConfig`, `OAIConfig`) |
| [build.md](build.md) | Image build / push to lab registry |
| [operations.md](operations.md) | Telnet O1, bandwidth reconfig, INA-Infra GitOps notes |
| [operator-agent.md](operator-agent.md) | Operator ↔ backend **WebSocket**; UI HTTP; CPU/API |

Upstream Nephio catalog packages: [nephio-project/catalog/workloads/oai](https://github.com/nephio-project/catalog/tree/main/workloads/oai). Lab OAI topology: [`docs/oai.md`](../../../docs/oai.md).
