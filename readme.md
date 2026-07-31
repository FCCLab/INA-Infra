# Bring up INA-Infra

## Clone

**Option A — recursive clone (one step):**

```bash
git clone --recurse-submodules https://github.com/FCCLab/INA-Infra.git
cd INA-Infra
```

**Option B — clone, then init submodules:**

```bash
git clone https://github.com/FCCLab/INA-Infra.git
cd INA-Infra
git submodule update --init --recursive
```

Both commands fetch all submodules from GitHub (`FCCLab/INA-Infra-*`). Option A is equivalent to Option B.

**Already cloned — pull latest:**

```bash
./scripts/github/pull_github.sh
```

On the **lab testbed** (Config Sync push target), register Gitea remotes once:

```bash
./scripts/setup_lab_git_remotes.sh
```

Submodule layout: GitHub `FCCLab/INA-Infra-*` in `.gitmodules`; lab GitOps pushes to Gitea `nephio/*` via `push_git_repos.sh`; mirror everything to GitHub with `./scripts/github/push_github.sh`.

---

Current testbed IPs, MetalLB pools (`10.1.138.100`–`.199`), and VIPs: [docs/ip_plan.md](docs/ip_plan.md). Full doc index: [docs/readme.md](docs/readme.md). Topology: [bringup/00_testbed/readme.md](bringup/00_testbed/readme.md).

GitOps components (render → push → Config Sync): Flannel, Multus, MetalLB, Kubernetes Dashboard (NodePort **30443**), OAI operators + core (**central** only), OpenSpeedTest MetalLB VIPs. See [bringup/02_configsync/readme.md](bringup/02_configsync/readme.md).

## Cluster networking

| Layer | CIDR / address | Notes |
|-------|----------------|--------|
| **Pods** | `10.244.0.0/16` (node slice `/24`) | Flannel; pod IPs e.g. `10.244.0.x` |
| **Services** | `10.96.0.0/12` | ClusterIP; DNS at `10.96.0.10` |
| **DNS** | CoreDNS (`kube-dns`) | In-cluster names: `*.svc.cluster.local` |

Examples: `gitea.gitea.svc.cluster.local:3000`, `api.porch-system`, `resource-backend-controller-grpc-svc.backend-system.svc.cluster.local:9999`.

## Bring up steps

```
sudo kubeadm init --pod-network-cidr=10.244.0.0/16 --apiserver-advertise-address=10.1.101.10
```

```
kpt fn render storageclass-local-path
kpt live init storageclass-local-path    # first time only
kpt live apply storageclass-local-path --output=table
```

```
kpt fn render gitea
kpt live init gitea
kpt live apply gitea --reconcile-timeout 15m --output=table
```

```
kpt fn render porch
kpt live init porch
kpt live apply porch --reconcile-timeout=15m --output=table
```

```
kpt fn render nephio-operator
kpt live init nephio-operator
kpt live apply nephio-operator --reconcile-timeout=15m --output=table
```

```
kubectl apply -f  - <<EOF
apiVersion: v1
kind: Secret
metadata:
    name: git-user-secret
    namespace: nephio-system
type: kubernetes.io/basic-auth
stringData:
    username: nephio
    password: secret
EOF
```

Use the same Gitea credentials as `bringup/gitea/secret-git-user.yaml`. A separate `git-user-secret` exists in namespace **`gitea`** for the Gitea chart.

```
kpt fn render resource-backend
kpt live init resource-backend
kpt live apply resource-backend --reconcile-timeout=15m --output=table
```

```
kpt fn render network-config
kpt live init network-config
kpt live apply network-config --reconcile-timeout=15m --output=table
```

```
kpt fn render stock-repos
kpt live init stock-repos
kpt live apply stock-repos --reconcile-timeout=15m --output=table
```

```
kpt fn render webui
kpt live init webui
kpt live apply webui --reconcile-timeout=15m --output=table --inventory-policy=adopt
```

## Management cluster Git repos

Apply in order after the bringup steps above.

| Order | File | Role |
|-------|------|------|
| 1 | `000-gitea-repos.yaml` | One-shot Job: creates empty Gitea repos `mgmt`, `mgmt-staging`, and `central-repo` (`auto_init`) via `/api/v1/user/repos`. |
| 2 | `000-mgmt-repos.yaml` | Registers Porch deployment repos: `git-user-secret` in `default`, plus `mgmt-staging` (bootstrap) and `mgmt` (deployment) pointing at Gitea. |

```
kubectl apply -f 000-gitea-repos.yaml
kubectl wait --for=condition=complete job/gitea-init-repos -n gitea --timeout=120s
kubectl apply -f 000-mgmt-repos.yaml
kubectl wait --for=condition=Ready repositories.config.porch.kpt.dev/mgmt --timeout=120s
```

Git credentials in `000-mgmt-repos.yaml` match `bringup/gitea/secret-git-user.yaml` (`nephio` / `secret`).

| Gitea repo | Browse | Purpose |
|------------|--------|---------|
| **mgmt** | [http://10.1.132.200:3000/nephio/mgmt](http://10.1.132.200:3000/nephio/mgmt) | Management cluster deployment packages |
| **mgmt-staging** | [http://10.1.132.200:3000/nephio/mgmt-staging](http://10.1.132.200:3000/nephio/mgmt-staging) | Mgmt bootstrap / staging |
| **central-repo** | [http://10.1.132.200:3000/nephio/central-repo](http://10.1.132.200:3000/nephio/central-repo) | Central workload cluster packages |

Gitea UI: [http://10.1.132.200:3000](http://10.1.132.200:3000) (node IP on mgmt control plane; port **80** on the same host also works).

## Central workload cluster

Second kubeadm cluster on `10.1.101.22` (kubeconfig cluster name **central**). See [docs/new_cluster.md](docs/new_cluster.md).

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central
kubectl --context=central@central get nodes
kubectl --context=mgmt@mgmt get nodes
```

If you still have `~/.kube/config-core`, rename it: `mv ~/.kube/config-core ~/.kube/config-central`.

On the mgmt node, kubeadm leaves context `kubernetes-admin@kubernetes`. Rename for docs/scripts that use `mgmt@mgmt`:

```bash
./rename.sh mgmt mgmt ~/.kube/config
```

## Notes

- **kube-rbac-proxy**: use `quay.io/brancz/kube-rbac-proxy:v0.8.0` (not `gcr.io/kubebuilder/...`).
- **porch**: `bringup/porch` includes `0-functionconfigs.yaml` and flag fixes for current `nephio/*:latest` images.
- **Porch / function-runner logs** (`localhost:4318 connection refused`): OpenTelemetry has no collector in-cluster; safe to ignore or set `OTEL_SDK_DISABLED=true` on those deployments.
- **Local Registry**: See [utils/registry/registry.md](file:///home/fcp/nephio-network-slicing/utils/registry/registry.md) for registry configuration and pushing OAI images using [push_oai.sh](file:///home/fcp/nephio-network-slicing/utils/registry/push_oai.sh).
- **Bare metal provisioning** (Metal3/Ironic) is optional and documented separately; not required for this single-node lab.
