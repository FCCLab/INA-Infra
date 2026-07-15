# Nephio / Kubernetes lab docs

Platform docs for the multi-cluster Nephio testbed (not OAI slice-specific).

| Doc | Topic |
|-----|--------|
| [testbed.md](testbed.md) | Topology, sites, interconnect |
| [ip.md](ip.md) | Subnets, MetalLB VIPs, node IPs |
| [config_sync.md](config_sync.md) | GitOps (Gitea + Config Sync) |
| [mgmt.md](mgmt.md) | Mgmt bridge / `10.1.132.0/24` |
| [gitea.md](gitea.md) | Gitea on mgmt |
| [new_cluster.md](new_cluster.md) | Legacy central kubeadm notes |

OAI / network-slicing GitOps docs stay under [`docs/`](../../docs/) (`oai.md`, `oai-deployment.md`).
