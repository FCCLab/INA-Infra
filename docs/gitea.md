# Gitea on mgmt

Deploy Gitea on the **mgmt** cluster as platform infrastructure (outside Config Sync GitOps).

Gitea is **not** in `repos/mgmt/`. If you add it there, Config Sync will own it and can prune it when syncing other manifests (for example OpenSpeedTest only).

## Layout

```
bringup/01_gitea/
├── gitea.conf          # cluster access, host, port, manifest list
├── gitea.sh            # install / uninstall (kubectl apply)
├── manifests/          # Kubernetes YAML
│   ├── service-gitea.yaml
│   ├── statefulset-gitea.yaml
│   └── ...
└── manifests/storage/  # optional local-path StorageClass (--with-storage)
```

## Access (no MetalLB)

Gitea is published on the **mgmt control-plane node IP** (`10.1.132.200` by default), not a MetalLB VIP.

The Service uses `ClusterIP` with `externalIPs` set to that node address. kube-proxy forwards `10.1.132.200:3000` and `:80` to the Gitea pod.

| URL | Purpose |
|-----|---------|
| [http://10.1.132.200:3000](http://10.1.132.200:3000) | Web UI |
| [http://10.1.132.200](http://10.1.132.200) | Web UI (port 80) |
| `gitea.nephio.lab` | Same host (Pi-hole static DNS) |

Login: `nephio` / `secret` (see `gitea.conf`).

Use **HTTP git URLs**, e.g. `http://10.1.132.200:3000/nephio/central-repo.git`. Git over SSH on port 22 is not exposed on the node IP (conflicts with host SSH).

## Prerequisites

- mgmt cluster Ready; kubectl context `mgmt@mgmt`
- CNI running (pod network)
- `local-path` StorageClass (first install: use `--with-storage`)

## Install

```bash
./bringup/01_gitea/gitea.sh --with-storage   # first time (StorageClass + Gitea)
./bringup/01_gitea/gitea.sh                  # reinstall / upgrade manifests
```

`gitea.sh` reads `gitea.conf`, syncs `GITEA_HOST` / `GITEA_PORT` into the service and `ROOT_URL`, then `kubectl apply`s manifests in order.

### Configuration

Edit `gitea.conf`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `KUBECONFIG` | `~/.kube/config` | kubeconfig file |
| `KUBECONFIG_CONTEXT` | `mgmt@mgmt` | kubectl context |
| `GITEA_HOST` | `10.1.132.200` | mgmt node IP (`MGMT_API_IP`) |
| `GITEA_PORT` | `3000` | HTTP port |
| `GITEA_USER` / `GITEA_PASSWORD` | `nephio` / `secret` | UI login |

Override on the CLI:

```bash
./bringup/01_gitea/gitea.sh --kubeconfig ~/.kube/config --context mgmt@mgmt
./bringup/01_gitea/gitea.sh --config /path/to/other.conf
./bringup/01_gitea/gitea.sh -n              # dry-run
```

## Uninstall

Removes workloads; **keeps PVCs** (database and repos data).

```bash
./bringup/01_gitea/gitea.sh --uninstall
./bringup/01_gitea/gitea.sh -u
```

To wipe data after uninstall:

```bash
kubectl --context mgmt@mgmt delete pvc -n gitea --all
```

## After install

1. Config Sync repos + tokens: `./bringup/02_configsync/configsync.sh repos` then `tokens`
2. Push GitOps content: `./bringup/03_push_to_git_repos/push_git_repos.sh`

See [bringup/02_configsync/readme.md](../bringup/02_configsync/readme.md) for the full Config Sync flow.

## Troubleshooting

**Service has no reachable URL**

- Confirm pods: `kubectl --context mgmt@mgmt get pods,svc -n gitea`
- Confirm `externalIPs` on the service matches `GITEA_HOST` in `gitea.conf`
- Re-run `./bringup/01_gitea/gitea.sh`

**Gitea was deleted after mgmt Config Sync**

- Gitea was likely added under `repos/mgmt/` by mistake. Keep it here only; reinstall with `gitea.sh`, then recreate repos (steps above).

**PostgreSQL stuck on first boot**

- Delete PVCs once and reinstall: `kubectl delete pvc -n gitea --all`, then `./bringup/01_gitea/gitea.sh`
