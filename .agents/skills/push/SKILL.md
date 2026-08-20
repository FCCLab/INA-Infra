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
2. **Two-Tier Architecture**:
   - **GitHub (Primary Source & Upstream)**: `https://github.com/FCCLab/INA-Infra-<repo>.git`.
   - **Gitea (Local GitOps Sync for Config Sync)**: `http://10.1.132.200:3000/nephio/<repo>.git` (watched directly by Google Config Sync `RootSync`).

---

## Procedure

### Step 1: Push Cluster GitOps Repositories (Local Gitea + GitHub)

To synchronize all four cluster submodules (`repos/mgmt`, `repos/central-repo`, `repos/regional-repo`, `repos/edge-repo`):

```bash
cd /home/fcp/INA-Infra
./bringup/03_push_to_git_repos/push_gitea_gitops.sh
```

Or provide a custom commit message:
```bash
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(scope): your descriptive message"
```

To target a specific cluster only (e.g. `regional` or `edge`):
```bash
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(cctv): update code" regional
```

*(Note: `./bringup/03_push_to_git_repos/push_git_repos.sh` is preserved as a backward-compatible wrapper).*

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
   git push origin main
   git push gitea main
   ```
5. Rerun `./bringup/03_push_to_git_repos/push_gitea_gitops.sh` to confirm all 4 clusters are `Everything up-to-date`.

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
# Automated Config Sync status and pod check for any cluster (e.g. edge, regional, central, mgmt)
./scripts/check-configsync.sh edge
./scripts/check-configsync.sh regional
./scripts/check-configsync.sh central
./scripts/check-configsync.sh mgmt

# Or check RootSync directly via kubectl:
kubectl --context edge@edge -n config-management-system get rootsync
kubectl --context regional@regional -n config-management-system get rootsync
kubectl --context central@central -n config-management-system get rootsync
kubectl --context mgmt@mgmt -n config-management-system get rootsync
```

Status condition `Sync Completed` and `SYNCERRORCOUNT: 0` confirms the GitOps package is live and applied.
