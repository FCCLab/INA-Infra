---
name: push-gitops-repos
description: >-
  Pushes GitOps repos to lab Gitea (then GitHub mirror) so Config Sync
  RootSync applies them. Use when the user runs or asks for
  ./bringup/03_push_to_git_repos/push_git_repos.sh, push git repos, push
  to Gitea, sync GitOps, or after editing repos/mgmt|central-repo|regional-repo|edge-repo.
---

# Push GitOps repos

Config Sync reads **Gitea**, not the local working tree. After changing YAML under `repos/`, push with this script. Do not `git push` the submodules by hand.

## Command (canonical)

From the INA-Infra repo root:

```bash
./bringup/03_push_to_git_repos/push_git_repos.sh
```

That wrapper execs `ina-infra/backend/scripts/push_git_repos.sh`. Default with no args: **mgmt, central, regional, edge**.

```bash
./bringup/03_push_to_git_repos/push_git_repos.sh regional
./bringup/03_push_to_git_repos/push_git_repos.sh -m "cctv: drop CPU limit" regional
./bringup/03_push_to_git_repos/push_git_repos.sh -p          # pull-only
./bringup/03_push_to_git_repos/push_git_repos.sh -n          # dry-run
```

## What it does (per cluster repo)

1. `cd repos/<name>` (`mgmt`, `central-repo`, `regional-repo`, `edge-repo`)
2. Pull `gitea/main` (abort whole script on merge conflict)
3. `git add -A` and commit if dirty (`-m` or a default message)
4. Pull Gitea again
5. Push **Gitea** (`http://10.1.132.200:3000`, user `nephio`)
6. Mirror **GitHub** `origin`

Never push before a successful pull. Exit on conflict; do not invent a rebase.

## When to run it

- User asks to push GitOps / Gitea / Config Sync
- You edited `repos/*/namespaces/` or `repos/*/cluster/`
- INA-Infra PL Deploy already wrote files but RootSync still shows the old spec (e.g. CCTV `limits.cpu: 4`)

Pass only the clusters you changed when possible (`regional` for CCTV on regional).

## After push

```bash
export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge
./scripts/check-configsync.sh
```

RootSync period is ~15s. Confirm the live object matches the repo before debugging pods.

## Do not

- `kubectl apply` / `kubectl patch` as a substitute (Config Sync overwrites)
- Hand-push submodule remotes with a different remote URL
- Commit the parent INA-Infra repo unless the user asked (this script commits **inside** `repos/*` only)
