# Register central with Nephio (mgmt)

Catalog `kpt pkg get` URLs use **`@v6`** (not floating `main`), e.g. `.../catalog.git/nephio/core/configsync@v6`.

**kpt live:** After `cd <package>/`, use `kpt live init .` and `kpt live apply .`. Do not run `kpt live init <package>` from inside that directory (causes `invalid directory argument`).

## Step 2 — WorkloadCluster

```bash
kubectl --context=mgmt@mgmt apply -f 001-central-workloadcluster.yaml
kubectl --context=mgmt@mgmt get workloadclusters
```

## Step 3 — Kubeconfig secret on mgmt

```bash
kubectl --context=mgmt@mgmt create secret generic central-kubeconfig \
  --from-file=value=$HOME/.kube/config-central

kubectl --context=mgmt@mgmt get secret central-kubeconfig -o jsonpath='{.data.value}' | base64 -d | head -5
```

## Step 4 — Repository package (on **mgmt only**)

**Run on `10.1.101.10`** (or any host with mgmt kubeconfig). Do **not** apply this package on central — central has no `infra.nephio.org` / Porch CRDs.

Package directory name becomes the Gitea/Porch repo name. This lab uses **`central-repo`** (not `central`).

```bash
export KUBECONFIG=$HOME/.kube/config    # mgmt only; not config-central
kubectl config current-context          # should be mgmt@mgmt (or rename via ./rename.sh)

cd central   # repo path: nephio-network-slicing/central

kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/distros/sandbox/repository@v6 \
  central-repo

# Optional: fix Gitea URL in central-repo/repo-porch.yaml after render:
#   http://gitea.gitea.svc.cluster.local:3000/nephio/central-repo.git
#   http://10.1.101.10:3000/nephio/central-repo.git

kpt fn render central-repo

# First apply only:
kpt live init central-repo

# Re-renders / updates — skip init (already initialized). Do not run init again unless you use --force:
kpt live apply central-repo --reconcile-timeout=15m --output=table
```

If `kpt live init` says inventory already exists, **skip init** and only run `kpt live apply`.

If `no context exists with the name: mgmt@mgmt`, fix kubeconfig on this host:

```bash
./rename.sh mgmt mgmt ~/.kube/config
export KUBECONFIG=$HOME/.kube/config
```

### Verify (use name `central-repo`)

```bash
kubectl --context=mgmt@mgmt get repositories.config.porch.kpt.dev central-repo
kubectl --context=mgmt@mgmt get repositories.infra.nephio.org central-repo
kubectl --context=mgmt@mgmt get tokens.infra.nephio.org | grep central
```

Expected:

- Porch + infra `Repository` **`central-repo`** exist (READY / REPO_STATUS True).
- Tokens **`central-repo-access-token-configsync`** and **`central-repo-access-token-porch`** are True.

`kubectl get repositories ... central` returns **NotFound** — that name was never created.

### Align names with WorkloadCluster `central` (optional)

To use repo name **`central`** instead of **`central-repo`**, remove the applied package and re-fetch as package `central` (same kpt path), or add a rendered `WorkloadCluster` named `central` with `kpt.dev/injected-resource` before `kpt fn render` so set-values uses `spec.clusterName`.

For OAI PackageVariants, set **`downstream.repo: central-repo`** unless you rename the repo.

---

## Next steps (bootstrap central + OAI)

| Step | Cluster | What |
|------|---------|------|
| 5 | mgmt | Gitea repo `central-repo` exists |
| 6 | central | Config Sync |
| 7 | central | RootSync → Gitea `central-repo` |
| 8 | central | Workload CRDs (+ storage) |
| 9 | mgmt | OAI PackageVariants |
| 10 | central | Verify OAI pods |

Use **`export KUBECONFIG=$HOME/.kube/config`** on mgmt and **`$HOME/.kube/config-central`** (or `../run_central.sh kubectl …`) on central.

### Step 5 — Gitea repo `central-repo` (mgmt)

The infra `Repository` controller usually creates the Gitea repo. If push/sync fails, pre-create it (add `central-repo` to `000-gitea-repos.yaml` loop or create in Gitea UI), then:

```bash
export KUBECONFIG=$HOME/.kube/config
kubectl apply -f ../000-gitea-repos.yaml   # after adding central-repo to the job list
```

Check from mgmt:

```bash
curl -s -u nephio:secret http://gitea.gitea.svc.cluster.local:3000/api/v1/repos/nephio/central-repo | head
```

### Step 6 — Config Sync on central (`.22`)

Fetch (or refresh) from catalog **`@v6`**:

```bash
cd central
kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/nephio/core/configsync@v6 \
  configsync
cd configsync
kpt fn render .
kpt live init .    # first time only
export KUBECONFIG=$HOME/.kube/config-central
kpt live apply . --reconcile-timeout=15m --output=table
```

Or from mgmt host via SSH:

```bash
../run_central.sh bash -lc 'cd nephio-network-slicing/central/configsync && kpt fn render . && kpt live init . && export KUBECONFIG=$HOME/.kube/config-central && kpt live apply . --reconcile-timeout=15m --output=table'
```

Verify on central:

```bash
kubectl --context=central@central get pods -n config-management-system
kubectl --context=central@central get rootsyncs -A
```

**Pod stuck Pending?** On a single-node kubeadm cluster the control-plane **taint** blocks workloads:

```text
FailedScheduling: 0/1 nodes are available: 1 node(s) had untolerated taint(s)
```

Allow scheduling on the only node (same as typical single-node lab fix):

```bash
export KUBECONFIG=$HOME/.kube/config-central
kubectl taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-
kubectl get pods -n config-management-system -w
```

After the operator is Running, Config Sync installs `reconciler-manager` in the same namespace.

### Step 7 — RootSync on central

```bash
kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/nephio/optional/rootsync@v6 \
  rootsync
```

Lab patch (local files only — does not touch the cluster). If your shell prompt is already `central`, skip `cd central`.

```bash
./patch-rootsync.sh
cat rootsync/rootsync.yaml
```

| Field | Value |
|-------|--------|
| `metadata.name` | `central-repo` |
| `spec.git.repo` | `http://10.1.101.10:3000/nephio/central-repo.git` |
| `spec.git.auth` | `token` |
| `spec.git.secretRef.name` | `central-repo-access-token-configsync` |

Override Gitea host: `GITEA_HOST=172.18.0.200 ./patch-rootsync.sh`

Copy the Config Sync git token from **mgmt** into **central** `config-management-system` (if not already there):

```bash
export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central

kubectl --context=mgmt@mgmt get secret central-repo-access-token-configsync -n default -o yaml \
  | sed 's/namespace: default/namespace: config-management-system/' \
  | kubectl --context=central@central apply -f -
```

Apply RootSync on **central** (same kpt flow as [readme.md](../readme.md) bringup). Run **`cd rootsync`** before `kpt live` — do not `kpt live apply .` from the parent `central/` directory.

```bash
cd rootsync
kpt fn render .
kpt live init .    # first time only (creates resourcegroup.yaml here)
export KUBECONFIG=$HOME/.kube/config-central
kpt live apply . --reconcile-timeout=15m --output=table
```

If you see `error: no ResourceGroup object was provided` or `invalid directory argument`, you are not inside the package directory — run `cd rootsync` first, then `kpt live init .` (not `kpt live init rootsync`).

Verify:

```bash
kubectl --context=central@central get rootsyncs -n config-management-system
# SOURCEERRORCOUNT / SYNCERRORCOUNT should stay 0 after git is reachable
```

### Step 8 — Workload CRDs and storage on central

Required for OAI operators and network functions ([multiple-VM guide](https://docs.nephio.org/docs/guides/install-guides/install-on-multiple-vm/)):

```bash
export KUBECONFIG=$HOME/.kube/config-central

kpt pkg get --for-deployment \
  https://github.com/nephio-project/catalog.git/nephio/core/workload-crds@v6 \
  workload-crds
cd workload-crds
kpt fn render .
kpt live init .    # first time only (not `kpt live init workload-crds` while inside this dir)
export KUBECONFIG=$HOME/.kube/config-central
kpt live apply . --reconcile-timeout=15m --output=table
```

Optional PVCs (MySQL, etc.) — same as mgmt:

```bash
# from repo root bringup/
kpt fn render storageclass-local-path
kpt live init storageclass-local-path
kpt live apply storageclass-local-path --output=table
```

(Run storageclass with central kubeconfig if not already present on `.22`.)

### Step 9 — Deploy OAI database + operators (mgmt)

On **mgmt**, apply PackageVariants (adapt [test-infra OAI manifests](https://github.com/nephio-project/test-infra/tree/main/e2e/tests/oai)) so **`downstream.repo: central-repo`** and selectors match `WorkloadCluster` label `nephio.org/site-type: core`.

```bash
export KUBECONFIG=$HOME/.kube/config
export BRANCH=v6   # match catalog @v6 package revisions on mgmt

# Example: database PackageVariantSet targets WorkloadCluster site-type=core
# Clone/adjust 002-database.yaml and 002-operators.yaml from test-infra:
#   - downstream.repo: central-repo
#   - oai-cp-operators injector / repo name: central-repo

# envsubst < 002-database.yaml | kubectl apply -f -   # copy from test-infra/e2e/tests/oai, set downstream.repo: central-repo
# envsubst < 002-operators.yaml | kubectl apply -f -
```

Use **porchctl** or **Nephio Web UI** to propose → approve → publish package revisions into `central-repo.git`. Config Sync on central then applies them.

### Step 10 — Verify OAI on central

```bash
kubectl --context=central@central get ns | grep oai
kubectl --context=central@central get pods -n oai-core
kubectl --context=central@central get pods -n oai-cn-operators
```

Then deploy individual NF PackageVariants (OAI guide Step 5) with **`downstream.repo: central-repo`**.

---

## Registration complete checklist

- [ ] `WorkloadCluster/central` on mgmt
- [ ] `central-kubeconfig` secret on mgmt
- [ ] Porch + infra `Repository/central-repo` Ready on mgmt
- [ ] Config Sync pods Running on central
- [ ] `RootSync` on central syncing `central-repo.git` without errors
- [ ] `workload-crds` applied on central
- [ ] OAI `oai-core` / `oai-cn-operators` namespaces and pods on central

References: [Installing base Nephio components](https://docs.nephio.org/docs/guides/install-guides/common-components/), [multiple VM install](https://docs.nephio.org/docs/guides/install-guides/install-on-multiple-vm/), [OAI testbed](https://docs.nephio.org/docs/guides/user-guides/usecase-user-guides/exercise-2-oai/).
