---
name: push
description: >-
  Procedure for safely committing and pushing changes across INA-Infra and all
  four cluster GitOps repositories (mgmt, central, regional, edge) to local Gitea
  (10.1.132.200:3000) and remote GitHub mirrors. Use whenever the user asks to
  push code, sync GitOps repos, or run the repository push workflow.
---

# INA-Infra GitOps Push Workflow

This skill defines the canonical workflow for committing and pushing changes across the `INA-Infra` root repository and its four managed GitOps cluster repositories (`mgmt`, `central`, `regional`, `edge`).

---

## Important Rules

1. **Explicit Permission Required**: Never auto-commit or auto-push without explicit user request or approval.
2. **Two-Tier GitOps Architecture**:
   - **Gitea (Internal Primary Source of Truth)**: `http://10.1.132.200:3000/nephio/<repo>.git` (watched directly by Google Config Sync `RootSync`).
   - **GitHub (Public Mirror & Upstream)**: `https://github.com/FCCLab/INA-Infra-<repo>.git`.

---

## Procedure

### Step 1: Push Cluster GitOps Repositories (Gitea + GitHub)

To synchronize all four cluster submodules (`repos/mgmt`, `repos/central-repo`, `repos/regional-repo`, `repos/edge-repo`):

```bash
cd /home/fcp/INA-Infra
./bringup/03_push_to_git_repos/push_git_repos.sh
```

Or provide a custom commit message:
```bash
./bringup/03_push_to_git_repos/push_git_repos.sh -m "feat(scope): your descriptive message"
```

To target a specific cluster only (e.g. `regional` or `edge`):
```bash
./bringup/03_push_to_git_repos/push_git_repos.sh -m "feat(cctv): update code" regional
```

---

### Step 2: Handle Non-Fast-Forward / Merge Divergence

If GitHub mirror rejects with `non-fast-forward`:
1. Enter the specific submodule:
   ```bash
   cd /home/fcp/INA-Infra/repos/<cluster>-repo
   ```
2. Fetch and merge GitHub upstream:
   ```bash
   git fetch origin
   git merge origin/main -m "Merge origin/main into <cluster>-repo"
   ```
3. Resolve any deleted/renamed file conflicts (e.g. `git rm <old-file>`).
4. Push both to Gitea and GitHub:
   ```bash
   git push gitea main
   git push origin main
   ```
5. Rerun `./bringup/03_push_to_git_repos/push_git_repos.sh` to confirm all 4 clusters are `Everything up-to-date`.

---

### Step 3: Push Main INA-Infra Workspace Repository

When the user asks to push the root repository:

```bash
cd /home/fcp/INA-Infra
git status
git add <files>
git commit -m "<type>(<scope>): <subject>"
git push origin main
```

---

### Step 4: Verify Config Sync on Clusters

Verify that Google Config Sync has synchronized the commit on the target cluster(s):

```bash
# Check regional cluster
kubectl --kubeconfig ~/.kube/config-regional --context regional@regional -n config-management-system get rootsync

# Check edge cluster
kubectl --kubeconfig ~/.kube/config-edge --context edge@edge -n config-management-system get rootsync

# Check central cluster
kubectl --kubeconfig ~/.kube/config-central --context central@central -n config-management-system get rootsync

# Check mgmt cluster
kubectl --kubeconfig ~/.kube/config-mgmt --context mgmt@mgmt -n config-management-system get rootsync
```

Status condition `Sync Completed` and `SYNCERRORCOUNT: 0` confirms the GitOps package is live and applied.
