#!/usr/bin/env bash
# Set timezone + NTP on Nephio lab nodes, and align Kubernetes pods to the same
# local time (TZ env). Default: Asia/Singapore + sg.pool.ntp.org.
#
# Per host (via SSH + sudo):
#   1. timedatectl set-timezone <ZONE>  (updates /etc/localtime)
#   2. drop-in for systemd-timesyncd → Singapore NTP pool
#   3. timedatectl set-ntp true + enable/restart systemd-timesyncd
#
# Per cluster (kubectl, unless --hosts-only):
#   4. set TZ=<ZONE> on Deployments/StatefulSets in OAI namespaces
#      (overrides image default Europe/Paris so pod `date` matches host localtime)
#
# Usage:
#   ./scripts/update_timezone.sh                    # hosts + k8s
#   ./scripts/update_timezone.sh --status
#   ./scripts/update_timezone.sh --hosts-only edge usrp
#   ./scripts/update_timezone.sh --k8s-only
#   ./scripts/update_timezone.sh --timezone Asia/Singapore central
#   HOSTS="usrp edge-0" ./scripts/update_timezone.sh
#
# Requires passwordless sudo on targets (lab default).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

SSH_CFG="${SSH_CFG:-$REPO_ROOT/utils/ssh_config/config}"
SSH_OPTS=(-F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 -o RequestTTY=no)
TIMEZONE="${TIMEZONE:-Asia/Singapore}"
NTP_SERVERS="${NTP_SERVERS:-sg.pool.ntp.org ntp.ubuntu.com}"
FALLBACK_NTP="${FALLBACK_NTP:-0.ubuntu.pool.ntp.org 1.ubuntu.pool.ntp.org}"
STATUS_ONLY=0
DRY_RUN=0
DO_HOSTS=1
DO_K8S=1
# Namespaces where OAI (and related) pods need TZ aligned with host localtime.
K8S_NAMESPACES="${K8S_NAMESPACES:-ina-infra oai-slice-deployment oai-upf oai-cn oai-cn-operators}"
K8S_CONTEXTS="${K8S_CONTEXTS:-mgmt@mgmt central@central regional@regional edge@edge}"

# Default: every SSH-reachable node that is (or can be) a k8s worker/CP.
DEFAULT_HOSTS=(
  mgmt-0 mgmt-1
  central-0 central-1 gh82
  regional-0 regional-1
  edge-0 edge-1 edge-2 edge-3
  usrp
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [options] [cluster|host ...]

Set timezone / NTP on lab nodes and align pod localtime (TZ) with the host.

Targets (default: all known cluster nodes):
  cluster name → CP + worker SSH aliases (mgmt/central/regional/edge)
  host alias   → as in utils/ssh_config/config (e.g. usrp, gh82, edge-2)
  Or set HOSTS="usrp edge-0" to override.

Options:
  -s, --status              Only print timezone / NTP / clock (hosts + sample pods)
  -t, --timezone ZONE       Timezone (default: ${TIMEZONE})
      --ntp "SERVER ..."    Primary NTP servers (default: ${NTP_SERVERS})
      --hosts-only          Skip Kubernetes TZ patch
      --k8s-only            Skip SSH host updates
      --namespaces "..."    K8s namespaces to patch (default: ${K8S_NAMESPACES})
  -n, --dry-run             Print actions only
  -h, --help                Show this help

Notes:
  K8s patch sets TZ on OAI NF/RAN/UPF deployments only (staggered), not every
  workload — mass --all rollouts can OOM Multus.
  Also rewrites repos/** TZ env values; push to Gitea so Config Sync keeps them.

Examples:
  $(basename "$0")
  $(basename "$0") --status
  $(basename "$0") --k8s-only
  $(basename "$0") edge usrp
  $(basename "$0") --timezone Asia/Singapore central regional

Environment:
  SSH_CFG / SSH_CONFIG   SSH config (default: utils/ssh_config/config)
  TIMEZONE               Same as --timezone
  NTP_SERVERS            Same as --ntp
  HOSTS                  Space-separated host override
  K8S_NAMESPACES         Same as --namespaces
  K8S_CONTEXTS           kubectl contexts (default: mgmt/central/regional/edge)
  KUBECONFIG             Combined kubeconfigs if unset
EOF
}

ssh_host() {
  ssh "${SSH_OPTS[@]}" "$@"
}

ensure_kubeconfig() {
  if [[ -z "${KUBECONFIG:-}" ]]; then
    export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
  fi
}

resolve_targets() {
  local arg cluster
  local -a out=()
  if [[ -n "${HOSTS:-}" ]]; then
    # shellcheck disable=SC2206
    out=(${HOSTS})
    printf '%s\n' "${out[@]}"
    return
  fi
  if [[ $# -eq 0 ]]; then
    printf '%s\n' "${DEFAULT_HOSTS[@]}"
    return
  fi
  for arg in "$@"; do
    case "$arg" in
      mgmt)
        out+=(mgmt-0 mgmt-1)
        ;;
      central)
        out+=("${CLUSTER_CP_HOST[central]}" "${CLUSTER_WORKER_HOST[central]}" gh82)
        ;;
      regional)
        out+=("${CLUSTER_CP_HOST[regional]}" "${CLUSTER_WORKER_HOST[regional]}")
        ;;
      edge)
        out+=("${CLUSTER_CP_HOST[edge]}" "${CLUSTER_WORKER_HOST[edge]}" edge-2 edge-3 usrp)
        ;;
      *)
        out+=("$arg")
        ;;
    esac
  done
  printf '%s\n' "${out[@]}"
}

remote_payload() {
  # Vars expanded by local shell; remote body after REMOTE is literal except we
  # pass MODE/TIMEZONE/NTP via env on the ssh command line.
  cat <<'REMOTE'
set -euo pipefail
MODE="${MODE:-apply}"
TIMEZONE="${TIMEZONE:-Asia/Singapore}"
NTP_SERVERS="${NTP_SERVERS:-sg.pool.ntp.org ntp.ubuntu.com}"
FALLBACK_NTP="${FALLBACK_NTP:-0.ubuntu.pool.ntp.org 1.ubuntu.pool.ntp.org}"

host="$(hostname -s 2>/dev/null || hostname)"
tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
ntp="$(timedatectl show -p NTP --value 2>/dev/null || true)"
sync="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
lt="$(readlink -f /etc/localtime 2>/dev/null || readlink /etc/localtime 2>/dev/null || echo '?')"
now="$(date -R)"

if [[ "$MODE" == "status" ]]; then
  printf '%-12s  tz=%-16s  localtime=%-40s  ntp=%-3s  sync=%-3s  %s\n' \
    "$host" "${tz:-?}" "${lt:-?}" "${ntp:-?}" "${sync:-?}" "$now"
  exit 0
fi

echo "==> $host: timezone $TIMEZONE + /etc/localtime + NTP ($NTP_SERVERS)"

sudo mkdir -p /etc/systemd/timesyncd.conf.d
sudo tee /etc/systemd/timesyncd.conf.d/99-lab-ntp.conf >/dev/null <<CONF
[Time]
NTP=${NTP_SERVERS}
FallbackNTP=${FALLBACK_NTP}
CONF

sudo timedatectl set-timezone "$TIMEZONE"
# timedatectl updates /etc/localtime; keep /etc/timezone in sync for apps that read it.
echo "$TIMEZONE" | sudo tee /etc/timezone >/dev/null
sudo timedatectl set-ntp true

if systemctl list-unit-files systemd-timesyncd.service >/dev/null 2>&1; then
  sudo systemctl enable systemd-timesyncd.service >/dev/null 2>&1 || true
  sudo systemctl restart systemd-timesyncd.service
fi

sleep 1
tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
ntp="$(timedatectl show -p NTP --value 2>/dev/null || true)"
sync="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"
lt="$(readlink -f /etc/localtime 2>/dev/null || readlink /etc/localtime 2>/dev/null || echo '?')"
now="$(date -R)"
printf '    ok  tz=%s  localtime=%s  ntp=%s  sync=%s  %s\n' "$tz" "$lt" "$ntp" "$sync" "$now"
REMOTE
}

apply_host() {
  local host="$1" mode="$2"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] $host MODE=$mode TIMEZONE=$TIMEZONE"
    return 0
  fi
  if ! ssh_host -o ConnectTimeout=8 "$host" 'true' 2>/dev/null; then
    echo "error: cannot SSH to $host" >&2
    return 1
  fi
  # Export vars into remote environment for the payload.
  ssh_host "$host" \
    "MODE=$(printf '%q' "$mode") TIMEZONE=$(printf '%q' "$TIMEZONE") NTP_SERVERS=$(printf '%q' "$NTP_SERVERS") FALLBACK_NTP=$(printf '%q' "$FALLBACK_NTP") bash -s" \
    < <(remote_payload)
}

# Workloads whose logs should match host localtime (OAI NFs / RAN / UPF).
# Avoid `kubectl set env --all` — mass simultaneous rollouts overload Multus.
k8s_tz_targets() {
  local ctx="$1" ns="$2"
  kubectl --context "$ctx" -n "$ns" get deploy,sts -o name 2>/dev/null \
    | grep -E '/(amf-core|smf-core|nrf-core|ausf-core|udm-core|udr-core|upf-|oai-cu|oai-du|oai-ue|oai-flexric|oai-nr)' \
    || true
}

# True if any container already has TZ and it differs from desired.
k8s_needs_tz_patch() {
  local ctx="$1" ns="$2" res="$3"
  local cur
  cur="$(
    kubectl --context "$ctx" -n "$ns" get "$res" \
      -o jsonpath='{range .spec.template.spec.containers[*].env[?(@.name=="TZ")]}{.value}{"\n"}{end}' \
      2>/dev/null || true
  )"
  # No TZ in pod spec → image may still default to Europe/Paris; patch.
  if [[ -z "$cur" ]]; then
    return 0
  fi
  # Already correct on all containers that define TZ.
  if echo "$cur" | grep -qvFx "$TIMEZONE"; then
    return 0
  fi
  return 1
}

# Patch Deployments/StatefulSets so containers use host local timezone (TZ).
# OAI images default to TZ=Europe/Paris; without this, pod clocks stay on CEST.
apply_k8s_tz() {
  local mode="$1"
  local ctx ns rc=0 res patched delay
  local -a contexts namespaces targets
  delay="${K8S_TZ_STAGGER_SEC:-2}"

  ensure_kubeconfig
  # shellcheck disable=SC2206
  contexts=(${K8S_CONTEXTS})
  # shellcheck disable=SC2206
  namespaces=(${K8S_NAMESPACES})

  echo
  echo "Kubernetes TZ=${TIMEZONE} (contexts: ${contexts[*]}; ns: ${namespaces[*]})"

  for ctx in "${contexts[@]}"; do
    if ! kubectl --context "$ctx" get ns >/dev/null 2>&1; then
      echo "  skip $ctx (unreachable)"
      continue
    fi
    for ns in "${namespaces[@]}"; do
      if ! kubectl --context "$ctx" get ns "$ns" >/dev/null 2>&1; then
        continue
      fi
      if [[ "$mode" == "status" ]]; then
        local sample
        sample="$(
          kubectl --context "$ctx" -n "$ns" get pods \
            --field-selector=status.phase=Running \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
        )"
        if [[ -n "$sample" ]]; then
          local cname pod_date pod_tz
          cname="$(
            kubectl --context "$ctx" -n "$ns" get pod "$sample" \
              -o jsonpath='{.spec.containers[0].name}' 2>/dev/null || true
          )"
          pod_date="$(
            kubectl --context "$ctx" -n "$ns" exec "$sample" -c "$cname" -- date -R 2>/dev/null || echo '?'
          )"
          pod_tz="$(
            kubectl --context "$ctx" -n "$ns" exec "$sample" -c "$cname" -- sh -c 'echo "${TZ:-unset}"' 2>/dev/null || echo '?'
          )"
          printf '  %-18s %-22s pod=%-40s TZ=%-16s %s\n' \
            "$ctx" "$ns/$sample" "$cname" "$pod_tz" "$pod_date"
        fi
        continue
      fi

      mapfile -t targets < <(k8s_tz_targets "$ctx" "$ns")
      [[ ${#targets[@]} -gt 0 ]] || continue

      for res in "${targets[@]}"; do
        if ! k8s_needs_tz_patch "$ctx" "$ns" "$res"; then
          echo "  $ctx $ns/$res: TZ already $TIMEZONE"
          continue
        fi
        if [[ "$DRY_RUN" -eq 1 ]]; then
          echo "[dry-run] kubectl --context $ctx -n $ns set env $res TZ=$TIMEZONE"
          continue
        fi
        patched="$(
          kubectl --context "$ctx" -n "$ns" set env "$res" "TZ=${TIMEZONE}" 2>&1 || true
        )"
        if echo "$patched" | grep -qiE '^error'; then
          echo "  warn: $ctx $ns/$res: $patched" >&2
          rc=1
        else
          echo "  $ctx $ns/$res: TZ=$TIMEZONE"
          sleep "$delay"
        fi
      done
    done
  done
  return "$rc"
}

# Keep GitOps manifests in sync so Config Sync does not revert Europe/Paris.
sync_gitops_tz() {
  local mode="$1"
  local root="$REPO_ROOT/repos"
  [[ -d "$root" ]] || return 0
  [[ "$mode" == "status" ]] && return 0

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] replace TZ Europe/Paris → $TIMEZONE under repos/"
    return 0
  fi

  echo
  echo "GitOps: setting container TZ value to $TIMEZONE under repos/"
  # Rewrite the value immediately after a TZ env name entry.
  find "$root" -type f -name '*.yaml' -print0 2>/dev/null \
    | xargs -0 -r grep -l 'name: TZ' 2>/dev/null \
    | while read -r f; do
        awk -v tz="$TIMEZONE" '
          BEGIN { prev="" }
          {
            if (prev ~ /^[[:space:]]*- name: TZ[[:space:]]*$/ && $0 ~ /^[[:space:]]*value:/) {
              sub(/value:.*/, "value: " tz)
            }
            print
            prev=$0
          }
        ' "$f" >"${f}.tznew" && mv "${f}.tznew" "$f"
      done
  echo "  updated YAML TZ entries (push via bringup/03_push_to_git_repos when ready)"
}

main() {
  local -a args=() targets=()
  local arg mode rc=0

  if [[ -n "${SSH_CONFIG:-}" && -z "${SSH_CFG_SET:-}" ]]; then
    SSH_CFG="$SSH_CONFIG"
    SSH_OPTS=(-F "$SSH_CFG" -o BatchMode=yes -o ConnectTimeout=15 -o RequestTTY=no)
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      -s|--status)
        STATUS_ONLY=1
        shift
        ;;
      -n|--dry-run)
        DRY_RUN=1
        shift
        ;;
      -t|--timezone)
        TIMEZONE="${2:?--timezone requires a value}"
        shift 2
        ;;
      --ntp)
        NTP_SERVERS="${2:?--ntp requires a value}"
        shift 2
        ;;
      --hosts-only)
        DO_K8S=0
        shift
        ;;
      --k8s-only)
        DO_HOSTS=0
        shift
        ;;
      --namespaces)
        K8S_NAMESPACES="${2:?--namespaces requires a value}"
        shift 2
        ;;
      --)
        shift
        args+=("$@")
        break
        ;;
      -*)
        echo "error: unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
      *)
        args+=("$1")
        shift
        ;;
    esac
  done

  if [[ "$DO_HOSTS" -eq 1 && ! -f "$SSH_CFG" ]]; then
    echo "error: SSH config not found: $SSH_CFG" >&2
    exit 1
  fi

  mode=apply
  [[ "$STATUS_ONLY" -eq 1 ]] && mode=status

  if [[ "$DO_HOSTS" -eq 1 ]]; then
    mapfile -t targets < <(resolve_targets "${args[@]+"${args[@]}"}" | awk 'NF && !seen[$0]++')
    if [[ ${#targets[@]} -eq 0 ]]; then
      echo "error: no host targets" >&2
      exit 1
    fi
    echo "Hosts (${#targets[@]}): ${targets[*]}"
    [[ "$mode" == "apply" ]] && echo "Timezone: $TIMEZONE  NTP: $NTP_SERVERS"
    echo

    local host
    for host in "${targets[@]}"; do
      if ! apply_host "$host" "$mode"; then
        rc=1
      fi
    done
  elif [[ ${#args[@]} -gt 0 ]]; then
    echo "note: host args ignored with --k8s-only" >&2
  fi

  if [[ "$DO_K8S" -eq 1 ]]; then
    if ! apply_k8s_tz "$mode"; then
      rc=1
    fi
    if [[ "$mode" == "apply" ]]; then
      sync_gitops_tz "$mode" || true
    fi
  fi

  if [[ "$mode" == "apply" && "$DO_K8S" -eq 1 && "$DRY_RUN" -eq 0 ]]; then
    echo
    echo "Pods will roll to pick up TZ=$TIMEZONE. Re-render OAI GitOps uses Asia/Singapore"
    echo "after scripts/render_* are updated; push repos so Config Sync keeps TZ."
  fi

  exit "$rc"
}

main "$@"
