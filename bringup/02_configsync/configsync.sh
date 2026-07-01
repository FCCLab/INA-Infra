#!/usr/bin/env bash
# Config Sync bringup: Gitea repos → git token secrets → operator + RootSync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=../../scripts/cluster_lib.sh
source "$REPO_ROOT/scripts/cluster_lib.sh"

CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/configsync.conf}"
STEP="all"
ACTION="install"
DRY_RUN=0
CLI_REMOTE=""
CLI_SKIP_UNTAINT=""
CLI_SKIP_OPERATOR=0
CLI_SKIP_ROOTSYNC=0
CLI_KUBECONFIG=""
CLI_CONTEXT=""
FETCH_KUBECONFIG=0

expand_path() {
  local p="$1"
  p="${p/#\~/$HOME}"
  printf '%s' "$p"
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [step] [options] [cluster ...]

Bring up Config Sync GitOps (default step: all).

Steps:
  repos     1. Create empty Gitea repos (mgmt, central-repo, regional-repo, ...)
  tokens    2. Create {repo}-access-token-configsync secrets on mgmt
  install   3. Install operator + RootSync on each cluster
  all       Run repos → tokens → install (default)

Default clusters: mgmt, central, regional, edge, ue.

Config:     ${CONFIG_FILE}
Operator:   manifests/operator/
Clusters:   manifests/{cluster}/

Prerequisites:
  - Gitea running: ./bringup/01_gitea/gitea.sh
  - CNI + clusters Ready (for install)

Install step per cluster:
  1. Apply manifests/operator/
  2. Copy token secret mgmt → config-management-system
  3. Apply manifests/{cluster}/rootsync.yaml

Options:
  --remote            Apply install via SSH to control plane (default)
  --local             Use local kubeconfig for install
  --fetch             SCP kubeconfig from CP before --local install
  --kubeconfig FILE   mgmt kubeconfig (overrides configsync.conf)
  --context NAME      mgmt kubectl context
  --skip-untaint      Do not remove control-plane NoSchedule taint
  --skip-operator     Apply RootSync only (operator already running)
  --skip-rootsync     Apply operator only
  --uninstall, -u     Remove RootSync + operator (install step only)
  --config FILE       Config file (default: configsync.conf)
  -n, --dry-run       Print actions only
  -h, --help          Show this help

Examples:
  $(basename "$0")
  $(basename "$0") repos
  $(basename "$0") tokens central regional
  $(basename "$0") install --local --fetch mgmt
  $(basename "$0") -n all

Verify:
  ./scripts/check-configsync.sh
EOF
}

load_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "error: config not found: ${CONFIG_FILE}" >&2
    exit 1
  fi
  # shellcheck source=configsync.conf
  source "$CONFIG_FILE"

  OPERATOR_MANIFESTS_DIR_ABS="${OPERATOR_MANIFESTS_DIR_ABS:-$SCRIPT_DIR/${OPERATOR_MANIFESTS_DIR:-manifests/operator}}"
  CLUSTER_MANIFESTS_DIR_ABS="${CLUSTER_MANIFESTS_DIR_ABS:-$SCRIPT_DIR/${CLUSTER_MANIFESTS_DIR:-manifests}}"
  SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

  MGMT_KUBECONFIG="$(expand_path "${CLI_KUBECONFIG:-${MGMT_KUBECONFIG:-${KUBECONFIG:-$(local_kubeconfig_path mgmt)}}}")"
  MGMT_CTX="${CLI_CONTEXT:-${MGMT_CTX:-mgmt@mgmt}}"

  GITEA_HOST="${GITEA_HOST:-$MGMT_API_IP}"
  GITEA_PORT="${GITEA_PORT:-3000}"
  GITEA_ORG="${GITEA_ORG:-nephio}"
  GITEA_USER="${GITEA_USER:-nephio}"
  GITEA_PASSWORD="${GITEA_PASSWORD:-secret}"
  GIT_BRANCH="${GIT_BRANCH:-main}"
  TOKEN_NAMESPACE="${TOKEN_NAMESPACE:-default}"
  POD_READY_TIMEOUT="${POD_READY_TIMEOUT:-600s}"
  GITEA_API_URL="http://${GITEA_HOST}:${GITEA_PORT}/api/v1"

  if [[ -n "$CLI_REMOTE" ]]; then
    REMOTE="$CLI_REMOTE"
  fi
  REMOTE="${REMOTE:-1}"
  if [[ -n "$CLI_SKIP_UNTAINT" ]]; then
    SKIP_UNTAINT="$CLI_SKIP_UNTAINT"
  fi
  SKIP_UNTAINT="${SKIP_UNTAINT:-0}"
  SKIP_OPERATOR="${SKIP_OPERATOR:-0}"
  SKIP_ROOTSYNC="${SKIP_ROOTSYNC:-0}"
}

validate_cluster() {
  local cluster="$1"
  local c
  for c in "${ALL_CONFIGSYNC_CLUSTERS[@]}"; do
    [[ "$c" == "$cluster" ]] && return 0
  done
  echo "error: unknown cluster '${cluster}' (expected: ${ALL_CONFIGSYNC_CLUSTERS[*]})" >&2
  return 1
}

validate_mgmt_access() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  if [[ ! -f "$MGMT_KUBECONFIG" ]]; then
    echo "error: mgmt kubeconfig not found: ${MGMT_KUBECONFIG}" >&2
    exit 1
  fi
  if ! kubectl --kubeconfig="$MGMT_KUBECONFIG" config get-contexts "$MGMT_CTX" >/dev/null 2>&1; then
    echo "error: mgmt context ${MGMT_CTX} not found in ${MGMT_KUBECONFIG}" >&2
    exit 1
  fi
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

cluster_kubectl() {
  local cluster="$1"
  shift
  if [[ "$REMOTE" == "1" ]]; then
    remote_kubectl "$cluster" "$@"
  else
    ensure_local_kubeconfig "$cluster" || return 1
    kubectl_cluster "$cluster" "$@"
  fi
}

apply_paths() {
  local cluster="$1"
  shift
  local -a paths=("$@")

  if [[ ${#paths[@]} -eq 0 ]]; then
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl apply ${paths[*]}"
    return 0
  fi

  if [[ "$cluster" == "mgmt" && "$REMOTE" != "1" ]]; then
    kubectl_mgmt apply "${paths[@]}"
  else
    local i f
    for ((i = 0; i < ${#paths[@]}; i++)); do
      [[ "${paths[$i]}" != "-f" ]] && continue
      f="${paths[$((i + 1))]}"
      cluster_kubectl "$cluster" apply -f - <"$f"
    done
  fi
}

delete_paths() {
  local cluster="$1"
  shift
  local -a paths=("$@")

  if [[ ${#paths[@]} -eq 0 ]]; then
    return 0
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl delete ${paths[*]} --ignore-not-found"
    return 0
  fi

  if [[ "$cluster" == "mgmt" && "$REMOTE" != "1" ]]; then
    kubectl_mgmt delete "${paths[@]}" --ignore-not-found
  else
    local i f
    for ((i = 0; i < ${#paths[@]}; i++)); do
      [[ "${paths[$i]}" != "-f" ]] && continue
      f="${paths[$((i + 1))]}"
      cluster_kubectl "$cluster" delete -f - --ignore-not-found <"$f"
    done
  fi
}

build_paths() {
  local dir="$1"
  shift
  local -a files=("$@")
  local -a paths=()
  local f

  for f in "${files[@]}"; do
    if [[ ! -f "${dir}/${f}" ]]; then
      echo "error: manifest not found: ${dir}/${f}" >&2
      return 1
    fi
    paths+=("-f" "${dir}/${f}")
  done
  printf '%s\0' "${paths[@]}"
}

gitea_api() {
  curl -fsS -u "${GITEA_USER}:${GITEA_PASSWORD}" \
    -H "Content-Type: application/json" \
    "$@"
}

gitea_repo_exists() {
  local repo_name="$1"
  gitea_api "${GITEA_API_URL}/repos/${GITEA_ORG}/${repo_name}" >/dev/null 2>&1
}

create_gitea_repo() {
  local cluster="$1"
  local repo_name
  repo_name="$(cluster_gitea_repo_name "$cluster")"

  echo "==> [${cluster}] Gitea repo ${GITEA_ORG}/${repo_name}"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: POST ${GITEA_API_URL}/user/repos (name=${repo_name})"
    return 0
  fi

  if gitea_repo_exists "$repo_name"; then
    echo "    already exists"
    return 0
  fi

  gitea_api -X POST "${GITEA_API_URL}/user/repos" \
    -d "{\"name\":\"${repo_name}\",\"auto_init\":true,\"private\":false,\"default_branch\":\"${GIT_BRANCH}\"}"
  echo "    created"
}

create_token_on_mgmt() {
  local cluster="$1"
  local repo_name secret_name
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  secret_name="${repo_name}-access-token-configsync"

  echo "==> [${cluster}] token secret ${secret_name} (namespace ${TOKEN_NAMESPACE})"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl apply secret ${secret_name}"
    return 0
  fi

  kubectl_mgmt apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${secret_name}
  namespace: ${TOKEN_NAMESPACE}
type: Opaque
stringData:
  username: ${GITEA_USER}
  token: ${GITEA_PASSWORD}
EOF
}

step_repos() {
  local cluster
  echo "==> Step 1: create Gitea repos"
  echo
  for cluster in "$@"; do
    create_gitea_repo "$cluster" || return 1
  done
}

step_tokens() {
  local cluster
  echo "==> Step 2: git token secrets on mgmt"
  echo
  validate_mgmt_access
  for cluster in "$@"; do
    create_token_on_mgmt "$cluster" || return 1
  done
}

sync_rootsync_manifest() {
  local cluster="$1"
  local repo_name="$2"
  local rootsync_file="${CLUSTER_MANIFESTS_DIR_ABS}/${cluster}/rootsync.yaml"
  local git_url="http://${GITEA_HOST}:${GITEA_PORT}/${GITEA_ORG}/${repo_name}.git"

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: write ${rootsync_file} (repo=${git_url})"
    return 0
  fi

  mkdir -p "${CLUSTER_MANIFESTS_DIR_ABS}/${cluster}"
  python3 - "$rootsync_file" "$repo_name" "$git_url" "$GIT_BRANCH" <<'PY'
import sys
from pathlib import Path

path, name, repo, branch = sys.argv[1:5]
secret = f"{name}-access-token-configsync"
Path(path).write_text(
    f"""apiVersion: configsync.gke.io/v1beta1
kind: RootSync
metadata:
  name: {name}
  namespace: config-management-system
spec:
  sourceFormat: unstructured
  git:
    repo: {repo}
    branch: {branch}
    auth: token
    secretRef:
      name: {secret}
    period: 15s
"""
)
PY
}

untaint_control_plane() {
  local cluster="$1"
  echo "==> [${cluster}] allow scheduling on control-plane nodes"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: kubectl taint nodes --all control-plane-"
    return 0
  fi
  cluster_kubectl "$cluster" taint nodes --all node-role.kubernetes.io/control-plane:NoSchedule- 2>/dev/null \
    || cluster_kubectl "$cluster" taint nodes --all node-role.kubernetes.io/master:NoSchedule- 2>/dev/null \
    || echo "    (no control-plane taint or already removed)"
}

wait_for_token_on_mgmt() {
  local repo_name="$1"
  local i

  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi

  for i in $(seq 1 6); do
    if kubectl_mgmt get secret "${repo_name}-access-token-configsync" -n "$TOKEN_NAMESPACE" >/dev/null 2>&1; then
      return 0
    fi
    [[ "$i" -eq 1 ]] && echo "    waiting for ${repo_name}-access-token-configsync on mgmt ..."
    sleep 5
  done

  echo "error: token secret ${repo_name}-access-token-configsync missing on mgmt — run: $(basename "$0") tokens" >&2
  return 1
}

copy_token_to_cluster() {
  local cluster="$1"
  local repo_name secret_name yaml_pipe target_ns
  repo_name="$(cluster_gitea_repo_name "$cluster")"
  secret_name="${repo_name}-access-token-configsync"
  target_ns="config-management-system"

  echo "==> [${cluster}] copy git token mgmt → ${secret_name}"
  wait_for_token_on_mgmt "$repo_name" || return 1

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "    dry-run: copy secret ${secret_name} to ${target_ns}"
    return 0
  fi

  # Nephio Token CR may own a basic-auth secret; delete so type can be replaced.
  cluster_kubectl "$cluster" delete token.infra.nephio.org "$secret_name" -n "$target_ns" \
    --ignore-not-found --wait=false 2>/dev/null || true
  cluster_kubectl "$cluster" delete secret "$secret_name" -n "$target_ns" \
    --ignore-not-found --wait=false 2>/dev/null || true
  if cluster_kubectl "$cluster" get token.infra.nephio.org "$secret_name" -n "$target_ns" >/dev/null 2>&1; then
    cluster_kubectl "$cluster" patch token.infra.nephio.org "$secret_name" -n "$target_ns" \
      -p '{"metadata":{"finalizers":null}}' --type=merge 2>/dev/null || true
  fi
  sleep 2

  yaml_pipe="$(kubectl_mgmt get secret "$secret_name" -n "$TOKEN_NAMESPACE" -o yaml \
    | python3 -c "
import sys, yaml
d = yaml.safe_load(sys.stdin)
m = d.get('metadata', {})
for k in ('uid','resourceVersion','creationTimestamp','managedFields','ownerReferences','annotations'):
    m.pop(k, None)
m['name'] = '${secret_name}'
m['namespace'] = '${target_ns}'
d['metadata'] = m
yaml.safe_dump(d, sys.stdout, default_flow_style=False)
")"

  if [[ "$cluster" == "mgmt" && "$REMOTE" != "1" ]]; then
    printf '%s\n' "$yaml_pipe" | kubectl_mgmt apply -f -
  else
    printf '%s\n' "$yaml_pipe" | cluster_kubectl "$cluster" apply -f -
  fi
}

wait_for_operator() {
  local cluster="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  echo "==> [${cluster}] wait for Config Sync pods"
  cluster_kubectl "$cluster" wait --for=condition=ready pod \
    -n config-management-system --timeout="${POD_READY_TIMEOUT}" 2>/dev/null \
    || cluster_kubectl "$cluster" get pods -n config-management-system 2>/dev/null || true
}

install_on_cluster() {
  local cluster="$1"
  local repo_name op_paths cluster_paths
  local -a op_path_arr cluster_path_arr

  repo_name="$(cluster_gitea_repo_name "$cluster")"

  if [[ "$REMOTE" == "1" ]] && ! cluster_ssh_reachable "$cluster"; then
    return 1
  fi

  if [[ "$SKIP_ROOTSYNC" != "1" && "$DRY_RUN" != "1" && "$ACTION" == "install" ]]; then
    if ! kubectl_mgmt get secret "${repo_name}-access-token-configsync" -n "$TOKEN_NAMESPACE" >/dev/null 2>&1; then
      echo "error: [${cluster}] missing ${repo_name}-access-token-configsync on mgmt — run:" >&2
      echo "       ./bringup/02_configsync/configsync.sh tokens ${cluster}" >&2
      return 1
    fi
  fi

  echo "==> [${cluster}] Config Sync from ${CLUSTER_MANIFESTS_DIR_ABS}/${cluster}"

  if [[ "$SKIP_OPERATOR" != "1" ]]; then
    mapfile -d '' -t op_path_arr < <(build_paths "$OPERATOR_MANIFESTS_DIR_ABS" "${OPERATOR_MANIFESTS[@]}")
    apply_paths "$cluster" "${op_path_arr[@]}"
    if [[ "$SKIP_UNTAINT" != "1" ]]; then
      untaint_control_plane "$cluster"
    fi
    wait_for_operator "$cluster"
  fi

  if [[ "$SKIP_ROOTSYNC" == "1" ]]; then
    return 0
  fi

  sync_rootsync_manifest "$cluster" "$repo_name"
  mapfile -d '' -t cluster_path_arr < <(build_paths "${CLUSTER_MANIFESTS_DIR_ABS}/${cluster}" "${CLUSTER_MANIFESTS[@]}")
  copy_token_to_cluster "$cluster" || return 1
  apply_paths "$cluster" "${cluster_path_arr[@]}"

  if [[ "$DRY_RUN" != "1" ]]; then
    local rs_name="$repo_name"
    cluster_kubectl "$cluster" get rootsync "$rs_name" -n config-management-system 2>/dev/null || true
  fi
}

uninstall_on_cluster() {
  local cluster="$1"
  local -a cluster_path_arr op_path_arr
  local f rev_op rev_cluster

  echo "==> [${cluster}] uninstall Config Sync manifests"

  mapfile -d '' -t cluster_path_arr < <(build_paths "${CLUSTER_MANIFESTS_DIR_ABS}/${cluster}" "${CLUSTER_MANIFESTS[@]}")
  rev_cluster=()
  for ((i = ${#cluster_path_arr[@]} - 1; i >= 0; i--)); do
    rev_cluster+=("${cluster_path_arr[$i]}")
  done
  delete_paths "$cluster" "${rev_cluster[@]}"

  mapfile -d '' -t op_path_arr < <(build_paths "$OPERATOR_MANIFESTS_DIR_ABS" "${OPERATOR_MANIFESTS[@]}")
  rev_op=()
  for ((i = ${#op_path_arr[@]} - 1; i >= 0; i--)); do
    rev_op+=("${op_path_arr[$i]}")
  done
  delete_paths "$cluster" "${rev_op[@]}"
}

step_install() {
  local cluster mode
  mode="remote (SSH)"
  [[ "$REMOTE" == "0" ]] && mode="local kubeconfig"

  echo "==> Step 3: Config Sync install [${mode}]"
  echo

  if [[ "$REMOTE" == "1" && ! -f "$SSH_CONFIG" ]]; then
    echo "error: SSH config not found: $SSH_CONFIG" >&2
    return 1
  fi

  if [[ ! -d "$OPERATOR_MANIFESTS_DIR_ABS" ]]; then
    echo "error: operator manifests not found: ${OPERATOR_MANIFESTS_DIR_ABS}" >&2
    return 1
  fi

  validate_mgmt_access

  local failed=0
  for cluster in "$@"; do
    if [[ "$ACTION" == "uninstall" ]]; then
      if ! uninstall_on_cluster "$cluster"; then
        failed=1
      fi
    else
      if ! install_on_cluster "$cluster"; then
        failed=1
      fi
    fi
    echo
  done

  if [[ "$ACTION" != "uninstall" && "$DRY_RUN" != "1" ]]; then
    echo "Verify: ./scripts/check-configsync.sh"
  fi

  return "$failed"
}

if [[ $# -gt 0 && "$1" =~ ^(repos|tokens|install|all)$ ]]; then
  STEP="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote)
      CLI_REMOTE=1
      shift
      ;;
    --local)
      CLI_REMOTE=0
      shift
      ;;
    --fetch)
      FETCH_KUBECONFIG=1
      shift
      ;;
    --skip-untaint)
      CLI_SKIP_UNTAINT=1
      shift
      ;;
    --skip-operator)
      CLI_SKIP_OPERATOR=1
      shift
      ;;
    --skip-rootsync)
      CLI_SKIP_ROOTSYNC=1
      shift
      ;;
    --uninstall|-u)
      ACTION="uninstall"
      shift
      ;;
    --config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --kubeconfig)
      CLI_KUBECONFIG="$2"
      shift 2
      ;;
    --context)
      CLI_CONTEXT="$2"
      shift 2
      ;;
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      load_config
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

load_config

if [[ "$CLI_SKIP_OPERATOR" == "1" ]]; then
  SKIP_OPERATOR=1
fi
if [[ "$CLI_SKIP_ROOTSYNC" == "1" ]]; then
  SKIP_ROOTSYNC=1
fi

clusters=()
if [[ $# -eq 0 ]]; then
  clusters=("${ALL_CONFIGSYNC_CLUSTERS[@]}")
else
  for cluster in "$@"; do
    validate_cluster "$cluster" || exit 1
    clusters+=("$cluster")
  done
fi

failed=0
case "$STEP" in
  repos)
    if ! command -v curl >/dev/null 2>&1; then
      echo "error: curl not found in PATH" >&2
      exit 1
    fi
    step_repos "${clusters[@]}" || failed=1
    ;;
  tokens)
    if ! command -v kubectl >/dev/null 2>&1; then
      echo "error: kubectl not found in PATH" >&2
      exit 1
    fi
    step_tokens "${clusters[@]}" || failed=1
    ;;
  install)
    if ! command -v kubectl >/dev/null 2>&1; then
      echo "error: kubectl not found in PATH" >&2
      exit 1
    fi
    step_install "${clusters[@]}" || failed=1
    ;;
  all)
    if ! command -v curl >/dev/null 2>&1 || ! command -v kubectl >/dev/null 2>&1; then
      echo "error: curl and kubectl required for step all" >&2
      exit 1
    fi
    step_repos "${clusters[@]}" || failed=1
    echo
    step_tokens "${clusters[@]}" || failed=1
    echo
    step_install "${clusters[@]}" || failed=1
    ;;
  *)
    echo "error: unknown step '${STEP}' (use repos, tokens, install, or all)" >&2
    usage >&2
    exit 1
    ;;
esac

exit "$failed"
