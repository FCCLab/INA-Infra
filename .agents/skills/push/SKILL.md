---
name: push
description: >-
  Procedure for committing and pushing changes across INA-Infra and all four cluster
  GitOps submodules (mgmt, central-repo, regional-repo, edge-repo) to local Gitea
  (10.1.132.200:3000) and remote GitHub mirrors. Use whenever the user asks to
  push code, sync GitOps repos, or run the repository push workflow.
---

# INA-Infra GitOps Push Workflow

Synchronizes the `INA-Infra` root repository and its four managed GitOps cluster repositories (`mgmt`, `central-repo`, `regional-repo`, `edge-repo`) across **GitHub** (upstream primary) and **Gitea** (local GitOps target watched by Google Config Sync `RootSync`).

---

## 1. Quick Commands

### Sync All 4 Cluster GitOps Repositories (Gitea + GitHub)
```bash
cd /home/fcp/INA-Infra
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(scope): descriptive commit message"
```

### Sync a Single Cluster Repository
```bash
# Targets: mgmt | central | regional | edge
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "fix(ott): update server config" central
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "feat(cctv): update edge analyzer" edge
```

### Push Root Workspace Repository (`INA-Infra`)
```bash
cd /home/fcp/INA-Infra
git add <files>
git commit -m "feat(scope): descriptive message"
git push origin main
```

---

## 2. Merge Conflict / Non-Fast-Forward Resolution

If a cluster submodule push is rejected:
1. Enter the submodule directory:
   ```bash
   cd /home/fcp/INA-Infra/repos/<cluster>-repo
   ```
2. Pull and merge upstream GitHub:
   ```bash
   git fetch origin
   git merge origin/main -m "Merge origin/main into <cluster>-repo"
   ```
3. Push both remotes:
   ```bash
   git push origin main
   git push gitea main
   ```
4. Verify with `./bringup/03_push_to_git_repos/push_gitea_gitops.sh`.

---

## 3. Verify Google Config Sync Status

```bash
# Automated cluster verification script:
./scripts/check-configsync.sh edge
./scripts/check-configsync.sh regional
./scripts/check-configsync.sh central
./scripts/check-configsync.sh mgmt

# Or inspect RootSync directly:
kubectl --context=edge@edge -n config-management-system get rootsync
kubectl --context=regional@regional -n config-management-system get rootsync
kubectl --context=central@central -n config-management-system get rootsync
kubectl --context=mgmt@mgmt -n config-management-system get rootsync
```
*Expected: `SYNCCOMMIT` matches latest commit, `SYNCERRORCOUNT: 0`.*
