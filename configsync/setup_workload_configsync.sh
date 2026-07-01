#!/usr/bin/env bash
# Steps G–H: Config Sync operator + RootSync on workload clusters.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

MGMT_CTX="${MGMT_CTX:-mgmt@mgmt}"
MGMT_KUBECONFIG="${MGMT_KUBECONFIG:-${KUBECONFIG:-$HOME/.kube/config}}"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
CONFIGSYNC_PKG="${CONFIGSYNC_PKG:-$REPO_ROOT/central/configsync}"
ROOTSYNC_BASE="${ROOTSYNC_BASE:-$SCRIPT_DIR/cluster-rootsync}"
KPT_ROOTSYNC_URL="${KPT_ROOTSYNC_URL:-https://github.com/nephio-project/catalog.git/nephio/optional/rootsync@v6}"
GITEA_HOST="${GITEA_HOST:-10.1.132.51}"
GITEA_PORT="${GITEA_PORT:-3000}"
RECONCILE_TIMEOUT="${RECONCILE_TIMEOUT:-15m}"
REMOTE=1
FETCH_KUBECONFIG=0
SKIP_UNTAINT=0
SKIP_OPERATOR=0
SKIP_ROOTSYNC=0
DRY_RUN=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster ...]

Install Config Sync and RootSync on workload clusters (steps G–H in central/readme.md):

  1. kpt apply Config Sync operator (central/configsync)
  2. Allow scheduling on control-plane nodes (single-node lab)
  3. Copy {cluster}-repo git token from mgmt → workload cluster
  4. kpt apply RootSync pulling nephio/{cluster}-repo from Gitea

Default (no args): central, regional, edge, ue.

Prerequisites:
  - Step F: ./configsync/setup_cluster_repos.sh
  - mgmt context ${MGMT_CTX} with repo access tokens on mgmt
  - kpt + docker on this host (for kpt fn render)
  - SSH to control planes when using --remote (kubectl on CP, no kpt required)

Options:
  --remote        Apply on workload cluster via SSH to control plane (default)
  --local         Use local kubeconfig (~/.kube/config-{cluster})
  --fetch         SCP kubeconfig from control plane before --local apply
  --skip-untaint  Do not remove control-plane NoSchedule taint
  --skip-operator Install RootSync only (operator already applied)
  --skip-rootsync Install operator only
  -n, --dry-run   Print actions only
  -h, --help      Show this help

Environment:
  MGMT_CTX MGMT_KUBECONFIG  mgmt kubectl access (token copy source)
  GITEA_HOST GITEA_PORT     Gitea reachable from workload clusters (default: 10.1.132.51:3000)
  SSH_CONFIG                SSH config for --remote (default: utils/ssh_config/config)
  RECONCILE_TIMEOUT         kpt live apply timeout (default: 15m)

Examples:
  $(basename "$0") --remote central
  $(basename "$0") --local --fetch central
  $(basename "$0") -n regional edge

Verify:
  ./scripts/check-configsync.sh -c central@central -n central-repo
EOF
}

kubectl_mgmt() {
  kubectl --kubeconfig="$MGMT_KUBECONFIG" --context="$MGMT_CTX" "$@"
}

kubectl_cluster() {
  local cluster="$1"
  shift
  local kcfg ctx
  kcfg="$(local_kubeconfig_path "$cluster")"
  ctx="$(kube_context "$cluster")"
  kubectl --kubeconfig="$kcfg" --context="$ctx" "$@"
}

ssh_host() {
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=10 -o RequestTTY=no "$@"
}

# On control planes, bringup copies admin.conf to ~/.kube/config for the SSH user.
remote_kubectl() {
  local cluster="$1"
  shift
  local host
  host="$(cluster_cp_host "$cluster")"
  ssh_host "$host" "kubectl $(printf '%q ' "$@")"
}

cluster_ssh_reachable() {
  local cluster="$1"
  local host
  host="$(cluster_cp_host "$cluster")"
  if ssh_host "$host" "echo ok" >/dev/null 2>&1; then
    return 0
  fi
  echo "error: [${cluster}] cannot SSH to ${host}" >&2
  return 1
}

is_cluster_manifest() {
  local f="$1"
  grep -qE 'config\.kubernetes\.io/local-config:\s*"true"' "$f" 2>/dev/null && return 1
  grep -qE '^kind:\s*(ResourceGroup|StarlarkRun)' "$f" 2>/dev/null && return 1
  grep -qE '^kind:\s*Kptfile' "$f" 2>/dev/null && return 1
  return 0
}

collect_manifests() {
  local pkg_dir="$1"
  local f
  while IFS= read -r f; do
    is_cluster_manifest "$f" && printf '%s\n' "$f"
  done < <(find "$pkg_dir" -name '*.yaml' -type f | sort)
}

validate_cluster() {
  local cluster="$1"
  case "$cluster" in
    central|regional|edge|ue) return 0 ;;
    *)
      echo "error: unknown cluster '${cluster}' (expected central, regional, edge, or ue)" >&2
      return 1
      ;;
  esac
}

ensure_local_kubeconfig() {
  local cluster="$1"
  local kcfg host ctx
  kcfg="$(local_kubeconfig_path "$cluster")"
  ctx="$(kube_context "$cluster")"

  if [[ "$FETCH_KUBECONFIG" == "1" ]]; then
    host="$(cluster_cp_host "$cluster")"
    echo "==> [${cluster}] fetch kubeconfig from ${host}"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "    dry-run: scp ${host}:.kube/config -> ${kcfg}"
      return 0
    fi
    mkdir -p "${HOME}/.kube"
    scp -F "$SSH_CONFIG" -q "${host}:.kube/config" "$kcfg"
    chmod 600 "$kcfg"
    "$REPO_ROOT/scripts/rename.sh" "$cluster" "$cluster" "$kcfg"
  fi

  if [[ ! -f "$kcfg" ]]; then
    echo "error: [${cluster}] missing ${kcfg} (use --fetch or --remote)" >&2
    return 1
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  if ! kubectl --kubeconfig="$kcfg" config get-contexts "$ctx" >/dev/null 2>&1; then
    echo "error: [${cluster}] context ${ctx} not in ${kcfg}" >&2
    return 1
  fi
}

rootsync_package_path() {
  local cluster="$1"
  local repo_name
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  if [[ "$cluster" == "central" && -d "$REPO_ROOT/central/rootsync" ]]; then
    printf '%s' "$REPO_ROOT/central/rootsync"
  else
    printf '%s/%s' "$ROOTSYNC_BASE" "$repo_name"
  fi
}

patch_rootsync_package_context() {
  local pkg_dir="$1"
  local repo_name="$2"
  local ctx_file="$pkg_dir/package-context.yaml"
  [[ -f "$ctx_file" ]] || return 0

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: patch ${ctx_file} (repo=${repo_name} gitea=${GITEA_HOST}:${GITEA_PORT})"
    return 0
  fi

  python3 - "$ctx_file" "$repo_name" "$GITEA_HOST" "$GITEA_PORT" <<'PY'
import sys
from pathlib import Path

path, repo, host, port = sys.argv[1:5]
text = Path(path).read_text()
lines = text.splitlines()
out = []
in_data = False
seen = set()
for line in lines:
    if line.strip() == "data:":
        in_data = True
        out.append(line)
        continue
    if in_data and line and not line.startswith(" "):
        in_data = False
    if in_data:
        key = line.split(":", 1)[0].strip()
        if key in ("name", "clusterName", "giteaHost", "giteaPort"):
            seen.add(key)
            continue
    out.append(line)
if "data:" not in out:
    out.append("data:")
idx = out.index("data:") + 1
out[idx:idx] = [
    f"  name: {repo}",
    f"  clusterName: {repo}",
    f'  giteaHost: "{host}"',
    f'  giteaPort: "{port}"',
]
Path(path).write_text("\n".join(out) + "\n")
PY
}

fix_rootsync_yaml() {
  local pkg_dir="$1"
  local repo_name="$2"
  local yaml="$pkg_dir/rootsync.yaml"
  [[ -f "$yaml" ]] || return 0

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: normalize ${yaml}"
    return 0
  fi

  sed -i \
    -e "s/^  name: example-rootsync$/  name: ${repo_name}/" \
    -e "s/^  name: example-cluster-name$/  name: ${repo_name}/" \
    -e "s/^  name: example$/  name: ${repo_name}/" \
    -e "s|nephio/example-rootsync\\.git|nephio/${repo_name}.git|g" \
    -e "s|nephio/example-cluster-name\\.git|nephio/${repo_name}.git|g" \
    -e "s|nephio/example\\.git|nephio/${repo_name}.git|g" \
    -e "s/example-rootsync-access-token-configsync/${repo_name}-access-token-configsync/g" \
    -e "s/example-cluster-name-access-token-configsync/${repo_name}-access-token-configsync/g" \
    -e "s/example-access-token-configsync/${repo_name}-access-token-configsync/g" \
    -e "s|http://[0-9.]*:[0-9]*/nephio/${repo_name}\\.git|http://${GITEA_HOST}:${GITEA_PORT}/nephio/${repo_name}.git|g" \
    "$yaml"
}

ensure_rootsync_package() {
  local cluster="$1"
  local repo_name pkg_dir
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  pkg_dir="$(rootsync_package_path "$cluster")"

  if [[ -d "$pkg_dir" && -f "$pkg_dir/Kptfile" ]]; then
    echo "==> [${cluster}] rootsync package ${pkg_dir}"
    return 0
  fi

  if [[ "$cluster" == "central" ]]; then
    echo "error: [${cluster}] missing ${pkg_dir}" >&2
    return 1
  fi

  echo "==> [${cluster}] kpt pkg get rootsync ${repo_name}"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kpt pkg get --for-deployment ${KPT_ROOTSYNC_URL} ${repo_name}"
    return 0
  fi

  mkdir -p "$ROOTSYNC_BASE"
  (
    cd "$ROOTSYNC_BASE"
    # Skip --for-deployment (needs docker for gen-pkg-context); patch package-context below.
    kpt pkg get "$KPT_ROOTSYNC_URL" "$repo_name"
  )
}

apply_kpt_package_local() {
  local cluster="$1"
  local pkg_dir="$2"
  local label="$3"

  echo "==> [${cluster}] ${label} (local kpt live apply)"
  ensure_local_kubeconfig "$cluster" || return 1
  local kcfg ctx
  kcfg="$(local_kubeconfig_path "$cluster")"
  ctx="$(kube_context "$cluster")"

  (
    cd "$pkg_dir"
    if [[ ! -f inventory-template.yaml ]]; then
      KUBECONFIG="$kcfg" kubectl config use-context "$ctx" >/dev/null
      kpt live init .
    fi
    KUBECONFIG="$kcfg" kubectl config use-context "$ctx" >/dev/null
    kpt live apply . --reconcile-timeout="$RECONCILE_TIMEOUT" --output=table
  )
}

apply_kpt_package_remote() {
  local cluster="$1"
  local pkg_dir="$2"
  local label="$3"
  local host manifest

  host="$(cluster_cp_host "$cluster")"
  echo "==> [${cluster}] ${label} (remote kubectl apply via ${host})"

  cluster_ssh_reachable "$cluster" || return 1

  if ! ssh_host "$host" "kubectl version --client >/dev/null 2>&1"; then
    echo "error: [${cluster}] kubectl not usable on ${host} (~/.kube/config missing? run bringup)" >&2
    return 1
  fi

  while IFS= read -r manifest; do
    [[ -n "$manifest" ]] || continue
    echo "    apply ${manifest#${REPO_ROOT}/}"
    ssh_host "$host" "kubectl apply -f -" < "$manifest"
  done < <(collect_manifests "$pkg_dir")
}

apply_kpt_package() {
  local cluster="$1"
  local pkg_dir="$2"
  local label="$3"
  local repo_name="${4:-}"

  if [[ ! -d "$pkg_dir" ]]; then
    echo "error: [${cluster}] missing package ${pkg_dir}" >&2
    return 1
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "==> [${cluster}] ${label}"
    echo "    dry-run: kpt fn render ${pkg_dir} && kubectl apply (remote=${REMOTE})"
    return 0
  fi

  if ! (
    cd "$pkg_dir"
    kpt fn render .
  ); then
    echo "error: [${cluster}] kpt fn render failed for ${pkg_dir}" >&2
    return 1
  fi
  if [[ -n "$repo_name" ]]; then
    fix_rootsync_yaml "$pkg_dir" "$repo_name"
  fi

  if [[ "$REMOTE" == "1" ]]; then
    if ! apply_kpt_package_remote "$cluster" "$pkg_dir" "$label"; then
      return 1
    fi
    return 0
  fi

  apply_kpt_package_local "$cluster" "$pkg_dir" "$label"
}

untaint_control_plane() {
  local cluster="$1"
  echo "==> [${cluster}] allow scheduling on control-plane nodes"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule-"
    return 0
  fi

  if [[ "$REMOTE" == "1" ]]; then
    remote_kubectl "$cluster" taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null \
      || remote_kubectl "$cluster" taint nodes --all node-role.kubernetes.io/master:NoSchedule- 2>/dev/null \
      || echo "    (no control-plane taint or already removed)"
  else
    kubectl_cluster "$cluster" taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null \
      || kubectl_cluster "$cluster" taint nodes --all node-role.kubernetes.io/master:NoSchedule- 2>/dev/null \
      || echo "    (no control-plane taint or already removed)"
  fi
}

wait_for_token_on_mgmt() {
  local repo_name="$1"
  local i

  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  for i in $(seq 1 18); do
    if kubectl_mgmt get secret "${repo_name}-access-token-configsync" -n default >/dev/null 2>&1; then
      return 0
    fi
    [[ "$i" -eq 1 ]] && echo "    waiting for ${repo_name}-access-token-configsync on mgmt ..."
    sleep 10
  done

  echo "error: token secret ${repo_name}-access-token-configsync missing on mgmt — run setup_cluster_repos.sh" >&2
  return 1
}

copy_token_to_cluster() {
  local cluster="$1"
  local repo_name secret_name
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  secret_name="${repo_name}-access-token-configsync"

  echo "==> [${cluster}] copy git token mgmt → workload (${secret_name})"
  wait_for_token_on_mgmt "$repo_name" || return 1

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl get secret ${secret_name} on mgmt | apply on ${cluster}"
    return 0
  fi

  local yaml_pipe
  yaml_pipe="$(kubectl_mgmt get secret "$secret_name" -n default -o yaml \
    | python3 -c "
import sys, yaml
d = yaml.safe_load(sys.stdin)
m = d.get('metadata', {})
for k in ('uid','resourceVersion','creationTimestamp','managedFields','ownerReferences','annotations'):
    m.pop(k, None)
m['name'] = '${secret_name}'
m['namespace'] = 'config-management-system'
d['metadata'] = m
yaml.safe_dump(d, sys.stdout, default_flow_style=False)
")"

  if [[ "$REMOTE" == "1" ]]; then
    printf '%s\n' "$yaml_pipe" | remote_kubectl "$cluster" apply -f -
  else
    ensure_local_kubeconfig "$cluster" || return 1
    printf '%s\n' "$yaml_pipe" | kubectl_cluster "$cluster" apply -f -
  fi
}

install_on_cluster() {
  local cluster="$1"
  local repo_name pkg_dir

  if [[ "$REMOTE" == "1" ]] && ! cluster_ssh_reachable "$cluster"; then
    return 1
  fi

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  if [[ "$SKIP_ROOTSYNC" != "1" && "$DRY_RUN" != "1" ]]; then
    if ! kubectl_mgmt get secret "${repo_name}-access-token-configsync" -n default >/dev/null 2>&1; then
      echo "error: [${cluster}] missing ${repo_name}-access-token-configsync on mgmt — run step F first:" >&2
      echo "       ./configsync/setup_cluster_repos.sh ${cluster}" >&2
      return 1
    fi
  fi

  if [[ "$SKIP_OPERATOR" != "1" ]]; then
    if [[ ! -d "$CONFIGSYNC_PKG" ]]; then
      echo "error: Config Sync package missing: ${CONFIGSYNC_PKG}" >&2
      return 1
    fi
    if ! apply_kpt_package "$cluster" "$CONFIGSYNC_PKG" "Config Sync operator"; then
      echo "error: [${cluster}] Config Sync operator apply failed" >&2
      return 1
    fi
    if [[ "$SKIP_UNTAINT" != "1" ]]; then
      untaint_control_plane "$cluster"
    fi
  fi

  if [[ "$SKIP_ROOTSYNC" == "1" ]]; then
    return 0
  fi

  ensure_rootsync_package "$cluster" || return 1
  pkg_dir="$(rootsync_package_path "$cluster")"
  patch_rootsync_package_context "$pkg_dir" "$repo_name"
  copy_token_to_cluster "$cluster" || return 1
  apply_kpt_package "$cluster" "$pkg_dir" "RootSync (${repo_name})" "$repo_name"
}

print_summary() {
  local clusters=("$@")
  local cluster ctx repo_name
  if [[ "$DRY_RUN" == "1" ]]; then
    return
  fi

  echo "Config Sync status:"
  for cluster in "${clusters[@]}"; do
    ctx="$(kube_context "$cluster")"
    repo_name="$(cluster_gitea_repo_name "$cluster")"
    echo "  ${ctx}:"
    if [[ "$REMOTE" == "1" ]]; then
      remote_kubectl "$cluster" get pods -n config-management-system 2>/dev/null | head -5 || true
      remote_kubectl "$cluster" get rootsync "$repo_name" -n config-management-system 2>/dev/null || true
    else
      kubectl_cluster "$cluster" get pods -n config-management-system 2>/dev/null | head -5 || true
      kubectl_cluster "$cluster" get rootsync "$repo_name" -n config-management-system 2>/dev/null || true
    fi
    echo
  done
  echo "Full status: ./scripts/check-configsync.sh -c ${ctx} -n ${repo_name}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)
      REMOTE=1
      shift
      ;;
    --local)
      REMOTE=0
      shift
      ;;
    --fetch)
      FETCH_KUBECONFIG=1
      shift
      ;;
    --skip-untaint)
      SKIP_UNTAINT=1
      shift
      ;;
    --skip-operator)
      SKIP_OPERATOR=1
      shift
      ;;
    --skip-rootsync)
      SKIP_ROOTSYNC=1
      shift
      ;;
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster" || exit 1
    clusters+=("$cluster")
  done
fi

for cmd in kubectl kpt; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: ${cmd} not found in PATH" >&2
    exit 1
  fi
done

if [[ "$REMOTE" == "1" && ! -f "$SSH_CONFIG" ]]; then
  echo "error: SSH config not found: $SSH_CONFIG (use --local or set SSH_CONFIG)" >&2
  exit 1
fi

if [[ "$DRY_RUN" != "1" ]]; then
  if ! kubectl --kubeconfig="$MGMT_KUBECONFIG" config get-contexts "$MGMT_CTX" >/dev/null 2>&1; then
    echo "error: mgmt context ${MGMT_CTX} not found in ${MGMT_KUBECONFIG}" >&2
    exit 1
  fi
fi

mode="remote (SSH)"
[[ "$REMOTE" == "0" ]] && mode="local kubeconfig"

echo "Install Config Sync on workload clusters [${mode}]: ${clusters[*]}"
echo

failed=0
for cluster in "${clusters[@]}"; do
  if ! install_on_cluster "$cluster"; then
    failed=1
  fi
  echo
done

print_summary "${clusters[@]}"
exit "$failed"
