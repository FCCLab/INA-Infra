# Nephio two-cluster lab (mgmt + central)

Git-backed deployment: **mgmt** runs Nephio, Porch, and **Gitea**; **central** is the workload cluster and syncs packages from Gitea via Config Sync.

Catalog packages use **`@v6`** tags (not floating `main`).

**kpt live:** `cd` into the package directory, then `kpt live init .` and `kpt live apply .` (not `kpt live init <dirname>` from inside that directory).

---

## Lab topology

| Cluster | kubectl context | Node | API server | Role |
|---------|-----------------|------|------------|------|
| **mgmt** | `mgmt@mgmt` | `node-0` @ `10.1.132.200` | `https://10.1.132.200:6443` | Nephio, Porch, Gitea |
| **central** | `central@central` | `central-0` SSH @ `10.1.132.210`, API @ `10.1.137.110` | `https://10.1.137.110:6443` | Workload / OAI NFs |

| Service | URL |
|---------|-----|
| Gitea UI | `http://10.1.132.51:3000` or `http://10.1.132.51` (`nephio` / `secret`) |
| Nephio Web UI | `http://10.1.132.52` |
| Gitea in-cluster (from **mgmt** pods) | `http://gitea.gitea.svc.cluster.local:3000` |

### Cluster management via Gitea

Nephio uses **one Gitea repo per cluster** (plus `mgmt-staging` for bootstrap). Porch **Approve** writes packages into git; Config Sync pulls them onto the cluster.

```
                    ┌─────────────────────────────────────┐
                    │  mgmt cluster (Nephio, Porch, Gitea) │
                    └─────────────────────────────────────┘
                      │                    │
         nephio/mgmt  │                    │  nephio/mgmt-staging
         (GitOps for  │                    │  (bootstrap only — official
          mgmt itself)│                    │   sandbox puts CAPI cluster PKgs here)
                      │
                      │  WorkloadCluster "central"
                      ▼
                    ┌─────────────────────────────────────┐
                    │  central cluster (OAI workloads)     │
                    └─────────────────────────────────────┘
                      │
         nephio/central-repo  ◄── packages land here (database, operators, NFs)
```

| Gitea repo | URL | What it manages |
|------------|-----|-----------------|
| **mgmt** | [http://10.1.132.51:3000/nephio/mgmt](http://10.1.132.51:3000/nephio/mgmt) | **Mgmt cluster only** (optional GitOps for Nephio components on mgmt) |
| **mgmt-staging** | [http://10.1.132.51:3000/nephio/mgmt-staging](http://10.1.132.51:3000/nephio/mgmt-staging) | Bootstrap / staging (official sandbox: creates workload-cluster repos) |
| **central-repo** | [http://10.1.132.51:3000/nephio/central-repo](http://10.1.132.51:3000/nephio/central-repo) | **Central workload cluster** — OAI database, operators, core NFs |

**Common confusion:** `nephio/mgmt` does **not** manage the **central** workload cluster. For central, use **`nephio/central-repo`**.

Use the **Nephio Web UI** (`http://10.1.132.52`) for Propose / Approve; use **Gitea** to browse git history.

**Why are the repos empty (only `README.md`)?** Creating the repo (step **C**) does not add packages. Git content appears only after Porch **Approve**:

| Repo | Why empty | How to get first real content |
|------|-----------|-------------------------------|
| `mgmt` | Mgmt was installed via `bringup/` kpt, not from git | Optional: GitOps mgmt (below). *Not needed for central OAI.* |
| `mgmt-staging` | No bootstrap PackageVariantSets applied (official exercise `001-infra.yaml`) | Use full [Exercise 2](https://docs.nephio.org/docs/guides/user-guides/usecase-user-guides/exercise-2-oai/) sandbox, or ignore in this lab |
| **`central-repo`** | No package **Approved** yet | Step **J**: `002-database.yaml` → Web UI Propose/Approve `database` → folders appear in Gitea |

**To manage the central cluster** (what you want for OAI), watch **`central-repo`**, not `mgmt`:

```bash
# After approve, you should see e.g. database/ in git:
curl -fsS -u nephio:secret http://10.1.132.51:3000/api/v1/repos/nephio/central-repo/contents/ | grep '"name"'
kubectl --context=central@central get rootsyncs -n config-management-system
```

**Optional — GitOps the mgmt cluster via `nephio/mgmt`** (tokens + Config Sync on mgmt):

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl config use-context mgmt@mgmt
cd ~/nephio-network-slicing

# Tokens + infra Repository for mgmt (Porch repo already from 000-mgmt-repos.yaml)
kpt fn render mgmt    # needs docker, or apply rendered manifests in mgmt/ after hand-edit
kpt live init mgmt    # first time only
kpt live apply mgmt --reconcile-timeout=15m --output=table

# Config Sync + RootSync on mgmt (pull nephio/mgmt onto mgmt cluster)
cd bringup
kpt fn render configsync && kpt live init configsync && kpt live apply configsync --reconcile-timeout=15m

kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/nephio/optional/rootsync@v6 \
  rootsync-mgmt
cd rootsync-mgmt
# set package-context / rootsync git URL to http://10.1.132.51:3000/nephio/mgmt.git
kpt fn render . && kpt live init . && kpt live apply . --reconcile-timeout=15m
```

Then deploy a package via Web UI with deployment repo **`mgmt`**, or publish a PackageVariantSet that targets `mgmt`.

Use both kubeconfigs when working across clusters:

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central
kubectl config use-context mgmt@mgmt      # management
kubectl config use-context central@central  # workload
```

**On `node-0` after kubeadm**, `~/.kube/config` usually has context `kubernetes-admin@kubernetes`, not `mgmt@mgmt`. Either rename once (recommended):

```bash
cd ~/nephio-network-slicing
./rename.sh mgmt mgmt ~/.kube/config
kubectl config get-contexts    # should show mgmt@mgmt
```

Or skip `--context` on the mgmt node and use the default context:

```bash
kubectl apply -f 000-gitea-repos.yaml
kubectl wait --for=condition=complete job/gitea-init-repos -n gitea --timeout=120s
kubectl apply -f 000-mgmt-repos.yaml
```

For **central** kubeconfig:

```bash
./rename.sh central central ~/.kube/config-central
```

---

## Bring-up command list

Run from the **repo root** (`nephio-network-slicing/`) unless noted.

### A — Management cluster Kubernetes + platform

Only if **mgmt** is not already running. Details: [readme.md](../readme.md).

```bash
cd bringup

# storage, gitea, porch, nephio-operator, resource-backend, network-config, stock-repos, webui
kpt fn render storageclass-local-path && kpt live init storageclass-local-path && kpt live apply storageclass-local-path --output=table
kpt fn render gitea && kpt live init gitea && kpt live apply gitea --reconcile-timeout=15m --output=table
kpt fn render porch && kpt live init porch && kpt live apply porch --reconcile-timeout=15m --output=table
kpt fn render nephio-operator && kpt live init nephio-operator && kpt live apply nephio-operator --reconcile-timeout=15m --output=table

kubectl apply -f - <<'EOF'
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

kpt fn render resource-backend && kpt live init resource-backend && kpt live apply resource-backend --reconcile-timeout=15m --output=table
kpt fn render network-config && kpt live init network-config && kpt live apply network-config --reconcile-timeout=15m --output=table
kpt fn render stock-repos && kpt live init stock-repos && kpt live apply stock-repos --reconcile-timeout=15m --output=table
kpt fn render webui && kpt live init webui && kpt live apply webui --reconcile-timeout=15m --output=table --inventory-policy=adopt
```

Optional MetalLB utilities (dashboard, openspeedtest): [utils/kustomization.yaml](../utils/kustomization.yaml).

Verify mgmt:

```bash
kubectl --context=mgmt@mgmt get nodes
kubectl --context=mgmt@mgmt get pods -n nephio-system,porch-system,gitea
```

---

### B — Central cluster Kubernetes (node 0)

```bash
./central/bringup-central-0.sh
```

Creates node **`central-0`**, kubeconfig **`central@central`** in `~/.kube/config-central`.

Verify:

```bash
kubectl --context=central@central get nodes
kubectl --context=central@central get pods -n kube-system,kube-flannel
```

---

### C — Gitea: create empty repos (nothing in Git yet)

Nephio/Porch need **empty repos** in Gitea before registration. In this lab **`nephio` is the Gitea admin user**, not an organization — use `/api/v1/user/repos` (not `/api/v1/orgs/nephio/repos`, which returns **404**).

**From your workstation** (Gitea VIP):

```bash
GITEA_URL=http://10.1.132.51:3000
GITEA_USER=nephio
GITEA_PASS=secret

for repo in mgmt mgmt-staging central-repo; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -u "${GITEA_USER}:${GITEA_PASS}" -X POST \
    "${GITEA_URL}/api/v1/user/repos" \
    -H 'Content-Type: application/json' \
    -d "{\"name\":\"${repo}\",\"auto_init\":true,\"private\":false}")
  case "$code" in
    201) echo "created ${repo}" ;;
    409) echo "${repo} already exists" ;;
    *)   echo "failed ${repo} (HTTP ${code})" >&2 ;;
  esac
done
```

Verify (repos appear as `nephio/<name>` in the UI):

```bash
curl -fsS -u nephio:secret "${GITEA_URL}/api/v1/user/repos" | grep '"name"'
```

| Repo | Used by |
|------|---------|
| `mgmt` | mgmt cluster deployment packages (Porch on mgmt) |
| `mgmt-staging` | mgmt bootstrap / staging |
| `central-repo` | central workload cluster packages (Config Sync on central) |

If you prefer a cluster Job instead of `curl` from your workstation (run from **repo root**):

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central
kubectl --context=mgmt@mgmt apply -f 000-gitea-repos.yaml
kubectl --context=mgmt@mgmt wait --for=condition=complete job/gitea-init-repos -n gitea --timeout=120s
```

The job creates `mgmt`, `mgmt-staging`, and `central-repo` via `/api/v1/user/repos` (Gitea user `nephio`, not an org).

---

### D — Register mgmt deployment repos (Porch on mgmt)

```bash
kubectl --context=mgmt@mgmt apply -f 000-mgmt-repos.yaml
kubectl --context=mgmt@mgmt wait --for=condition=Ready repositories.config.porch.kpt.dev/mgmt --timeout=300s
```

Or apply equivalent `Repository` CRs pointing at:

- `http://gitea.gitea.svc.cluster.local:3000/nephio/mgmt-staging.git`
- `http://gitea.gitea.svc.cluster.local:3000/nephio/mgmt.git`

Credentials: `nephio` / `secret` (same as Gitea).

Verify:

```bash
kubectl --context=mgmt@mgmt get repositories.config.porch.kpt.dev mgmt mgmt-staging
```

---

### E — Register central with Nephio (on mgmt)

```bash
kubectl --context=mgmt@mgmt apply -f central/001-central-workloadcluster.yaml

# kubeconfig must use context central@central (see bringup-central-0.sh)
kubectl --context=mgmt@mgmt create secret generic central-kubeconfig \
  --from-file=value=$HOME/.kube/config-central \
  --dry-run=client -o yaml | kubectl --context=mgmt@mgmt apply -f -
```

Verify:

```bash
kubectl --context=mgmt@mgmt get workloadclusters central
kubectl --context=mgmt@mgmt get secret central-kubeconfig
```

---

### F — `central-repo` package (mgmt only)

Creates Porch + infra `Repository` for **`central-repo`** and access **tokens**. Do **not** apply on central (no Nephio CRDs there).

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl config use-context mgmt@mgmt
cd central

kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/distros/sandbox/repository@v6 \
  central-repo

kpt fn render central-repo
kpt live init central-repo    # first time only
kpt live apply central-repo --reconcile-timeout=15m --output=table
```

Verify on **mgmt**:

```bash
kubectl --context=mgmt@mgmt get repositories.config.porch.kpt.dev central-repo
kubectl --context=mgmt@mgmt get repositories.infra.nephio.org central-repo
kubectl --context=mgmt@mgmt get tokens.infra.nephio.org | grep central
```

Expected tokens (status **True**):

- `central-repo-access-token-configsync`
- `central-repo-access-token-porch`

If Gitea repo was missing, step **C** must succeed before this step.

---

### G — Config Sync on central

```bash
cd central/configsync
kpt fn render .
kpt live init .    # first time only
export KUBECONFIG=$HOME/.kube/config-central
kpt live apply . --reconcile-timeout=15m --output=table
```

**Single-node taint** — pods stay `Pending` until you allow scheduling on the control plane:

```bash
kubectl --context=central@central taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-
kubectl --context=central@central get pods -n config-management-system -w
```

Verify:

```bash
kubectl --context=central@central get pods -n config-management-system
```

---

### H — RootSync on central (pull `central-repo` from Gitea)

Patch local files (Gitea VIP reachable **from central**):

```bash
cd central
GITEA_HOST=10.1.132.51 GITEA_PORT=3000 ./patch-rootsync.sh
```

Copy Config Sync git token from **mgmt** → **central**:

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central

kubectl --context=mgmt@mgmt get secret central-repo-access-token-configsync -n default -o yaml \
  | sed 's/namespace: default/namespace: config-management-system/' \
  | kubectl --context=central@central apply -f -
```

Apply RootSync on **central**:

```bash
cd central/rootsync
kpt fn render .
kpt live init .    # first time only
export KUBECONFIG=$HOME/.kube/config-central
kpt live apply . --reconcile-timeout=15m --output=table
```

Verify:

```bash
kubectl --context=central@central get rootsyncs -n config-management-system
# SOURCEERRORCOUNT / SYNCERRORCOUNT should be 0
```

---

### I — Workload CRDs + storage on central

```bash
export KUBECONFIG=$HOME/.kube/config-central
cd central

kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/nephio/core/workload-crds@v6 \
  workload-crds
cd workload-crds
kpt fn render .
kpt live init .
kpt live apply . --reconcile-timeout=15m --output=table
```

Optional local-path storage (if not on central yet):

```bash
cd ../../bringup
kpt fn render storageclass-local-path
kpt live init storageclass-local-path
kubectl --context=central@central apply -f storageclass-local-path/   # or kpt live apply with KUBECONFIG=central
```

---

### J — OAI packages (optional, after above is green)

Push blueprint packages to Gitea, register Porch repo, deploy database PackageVariantSet on **mgmt**:

```bash
cd central
chmod +x push-oai-packages-to-gitea.sh sync-oai-packages-repo.sh reapply-database.sh

export GITEA_HOST=10.1.132.51 GITEA_PORT=3000 GITEA_USER=nephio GITEA_PASS=secret
./push-oai-packages-to-gitea.sh

export KUBECONFIG=$HOME/.kube/config
kubectl --context=mgmt@mgmt apply -f repo-oai-packages-gitea.yaml
./sync-oai-packages-repo.sh
kubectl --context=mgmt@mgmt apply -f 002-database.yaml
```

Publish `central-repo.database` via porchctl or Web UI — see [OAI section](#oai-database-and-operators) below.

Verify on **central**:

```bash
kubectl --context=central@central get ns | grep oai
kubectl --context=central@central get pods -n oai-core
```

---

## Quick status checks

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central

# Clusters
kubectl --context=mgmt@mgmt get nodes
kubectl --context=central@central get nodes

# Gitea repos (workstation)
curl -fsS -u nephio:secret http://10.1.132.51:3000/api/v1/user/repos | grep '"name"'

# Nephio registration
kubectl --context=mgmt@mgmt get workloadclusters,tokens.infra.nephio.org,repositories.infra.nephio.org | grep -E 'central|NAME'

# Central GitOps
kubectl --context=central@central get pods -n config-management-system
kubectl --context=central@central get rootsyncs -n config-management-system
```

---

## Registration checklist

- [ ] **mgmt** cluster Ready; Nephio + Porch + Gitea running
- [ ] **central** cluster Ready (`bringup-central-0.sh`); context `central@central`
- [ ] Gitea repos: `mgmt`, `mgmt-staging`, `central-repo`
- [ ] Porch repos `mgmt` / `mgmt-staging` Ready on mgmt
- [ ] `WorkloadCluster/central` + `central-kubeconfig` secret on mgmt
- [ ] `Repository/central-repo` + tokens Ready on mgmt
- [ ] Config Sync Running on central (taint removed if single-node)
- [ ] `RootSync` syncing `central-repo` without errors
- [ ] `workload-crds` on central
- [ ] (Optional) OAI database published → pods on central

---

## Exercise 2 OAI ([official guide](https://docs.nephio.org/docs/guides/user-guides/usecase-user-guides/exercise-2-oai/))

The upstream exercise deploys **OAI Core + RAN + UE** on a **single-VM sandbox**: mgmt plus three **KinD** workload clusters (**core**, **regional**, **edge**) created by Cluster API, with Containerlab inter-cluster networking and commits flowing through **`nephio/mgmt`** and **`nephio/mgmt-staging`**.

This repo is a **reduced bare-metal lab**: **mgmt** + one workload cluster **`central`** (kubeadm). Workload GitOps uses **`nephio/central-repo`**, not `nephio/mgmt`.

### Topology mapping

| Official exercise | This lab |
|-------------------|----------|
| mgmt (Nephio, Porch, Gitea) | **mgmt** @ `10.1.132.200` |
| core workload cluster | **`central`** @ `10.1.137.110` (`nephio.org/site-type: core`) |
| regional workload cluster | *not deployed* |
| edge workload cluster | *not deployed* |
| `nephio/mgmt` + `mgmt-staging` (CAPI bootstrap commits) | `mgmt` / `mgmt-staging` registered; **repos empty** until you GitOps mgmt (see above) |
| Per-cluster Gitea repos (core, regional, edge) | **`nephio/central-repo`** only |
| Upstream `oai-core-packages` on GitHub | **`nephio/oai-packages`** on local Gitea, branch **`v5`** |

### Official steps → this repo

| Official step | In this lab |
|---------------|-------------|
| **Step 1** — `001-infra.yaml` (CAPI core/regional/edge) | **B** + **E** — `bringup-central-0.sh`, `001-central-workloadcluster.yaml`, **F**–**H** (`central-repo` + Config Sync + RootSync). *No KinD/CAPI.* |
| **Step 2** — cluster status, kubeconfigs, Containerlab, VLANs, resource-backend networks, MetalLB per cluster | **Quick status checks** below. *Skip Containerlab / `001-network.yaml` / `network-topo.sh` unless you add multi-cluster networking.* |
| **Step 3** — `002-database.yaml`, `002-operators.yaml` | **J** — `push-oai-packages-to-gitea.sh`, `repo-oai-packages-gitea.yaml`, [`002-database.yaml`](002-database.yaml), [`003-operators-cp.yaml`](003-operators-cp.yaml) |
| **Step 4** — verify DB + operators on **core** | `kubectl --context=central@central get ns,pods -n oai-core,oai-cn-operators` |
| **Step 5** — `003-core-network.yaml` (NRF…SMF, UPF on **edge**) | [`004-core-network.yaml`](004-core-network.yaml) on **central** (CP NFs only; **no UPF** without an edge cluster) |
| **Step 6** — RAN (`004a`/`004b` on regional/edge) | *Not in this repo* — needs regional + edge clusters |
| **Step 7** — UE on edge | *Not in this repo* |
| **Step 8** — UE ↔ UPF ping | *Not in this repo* |

### OAI flow on this lab (after bring-up steps **A**–**I**)

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central
cd ~/nephio-network-slicing/central

# 1. Blueprints on Gitea + Porch repo (exercise Step 3 prep)
export GITEA_HOST=10.1.132.51 GITEA_PORT=3000
./push-oai-packages-to-gitea.sh
kubectl --context=mgmt@mgmt apply -f repo-oai-packages-gitea.yaml
./sync-oai-packages-repo.sh

# 2. Database PackageVariantSet (exercise 002-database → central-repo)
kubectl --context=mgmt@mgmt apply -f 002-database.yaml

# 3. Propose + approve each downstream revision (exercise uses porchctl / Web UI)
#    Web UI: http://10.1.132.52 → repo central-repo → package database → Propose → Approve
export PATH="$HOME/.local/bin:$PATH"
porchctl rpkg propose -n default central-repo.database.packagevariant-1
porchctl rpkg approve -n default central-repo.database.packagevariant-1

# 4. Operators, then core NFs (same propose/approve per package)
kubectl --context=mgmt@mgmt apply -f 003-operators-cp.yaml
# approve central-repo/oai-cp-operators …
kubectl --context=mgmt@mgmt apply -f 004-core-network.yaml
# approve NRF first, then UDM/UDR/AUSF/AMF/SMF …

# 5. Verify on workload cluster
kubectl --context=central@central get pods -n oai-core,oai-cn-operators
```

**Publish order matters** (same as the official guide): **database** → **operators** → **NRF** first, then other core NFs. Config Sync on **central** applies approved packages from `nephio/central-repo`.

### What you get vs full Exercise 2

| Capability | This lab |
|------------|----------|
| MySQL + OAI CP operators + NRF/UDM/UDR/AUSF/AMF/SMF on one cluster | Yes (when packages are approved) |
| UPF on edge, SMF↔UPF PFCP | No — needs edge cluster + `oai-up-operators` |
| CU-CP / CU-UP / DU / UE / E2E ping | No — needs regional + edge + inter-cluster L2/L3 |

To run the **full** [Exercise 2](https://docs.nephio.org/docs/guides/user-guides/usecase-user-guides/exercise-2-oai/) topology, use the Nephio single-VM sandbox (`test-infra/e2e`) or add regional/edge clusters and networking to this lab.

---

## OAI database and operators

On **mgmt**, after `central-repo` and `oai-packages` exist in Gitea:

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl --context=mgmt@mgmt apply -f 002-database.yaml
kubectl --context=mgmt@mgmt get packagevariantsets,packagevariants | grep -E 'database|NAME'
```

**Stuck?** Run `./reapply-database.sh` from `central/`.

**Publish database revision** (Porch: propose → approve).

Install `porchctl` once on the mgmt node (not included in cluster bringup):

```bash
mkdir -p ~/.local/bin
curl -fsSL -o /tmp/porchctl.tgz \
  https://github.com/nephio-project/porch/releases/download/v1.4.0/porchctl_1.4.0_linux_amd64.tar.gz
tar -xzf /tmp/porchctl.tgz -C /tmp porchctl
mv /tmp/porchctl ~/.local/bin/
chmod +x ~/.local/bin/porchctl
export PATH="$HOME/.local/bin:$PATH"
porchctl version
```

Wait until the downstream draft exists:

```bash
kubectl --context=mgmt@mgmt get packagerevision central-repo.database.packagevariant-1 -n default
```

Then propose and approve:

```bash
export PATH="$HOME/.local/bin:$PATH"
porchctl rpkg propose -n default central-repo.database.packagevariant-1
porchctl rpkg approve -n default central-repo.database.packagevariant-1
```

Or use Nephio Web UI (`http://10.1.132.52`): repo **`central-repo`** → package **`database`** → Propose → Approve.

`002-database.yaml` uses upstream **Gitea `oai-packages`** branch/workspace **`v5`**, downstream **`central-repo`**, selector `nephio.org/site-type: core`.

Operators: adapt `003-operators-cp.yaml` / `002-operators.yaml` from this repo; same propose/approve flow per package.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Pods **Pending** on central | `kubectl --context=central@central taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-` |
| `context mgmt@mgmt does not exist` | On mgmt node: `./rename.sh mgmt mgmt ~/.kube/config`, or omit `--context` and use default |
| `context central@central does not exist` | `./rename.sh central central ~/.kube/config-central` |
| Nephio **central** pending in UI | Complete steps **C** → **F** (Gitea repo + `central-repo` package + tokens) |
| Token controller / Gitea errors | Gitea service must expose port **3000** in-cluster (`bringup/gitea/service-gitea.yaml`) |
| RootSync git errors on central | Use Gitea VIP in `rootsync` (`GITEA_HOST=10.1.132.51`); copy token secret to `config-management-system` |
| `nephio/mgmt` empty in Gitea | Normal until Porch **Approve** publishes packages; mgmt was installed via `bringup/` kpt, not from git — see [central/readme.md](central/readme.md#cluster-management-via-gitea) |

**Remote central host:**

```bash
./cmd_central-0.sh kubectl get nodes
```

---

## References

- [Root readme — mgmt bringup](../readme.md)
- [new_cluster.md](../docs/new_cluster.md)
- [Nephio common components](https://docs.nephio.org/docs/guides/install-guides/common-components/)
- [Multiple VM install](https://docs.nephio.org/docs/guides/install-guides/install-on-multiple-vm/)
- [OAI testbed](https://docs.nephio.org/docs/guides/user-guides/usecase-user-guides/exercise-2-oai/)
