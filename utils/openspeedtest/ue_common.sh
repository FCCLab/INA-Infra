#!/usr/bin/env bash
# Shared helpers for OpenSpeedTest / UE tunnel utilities (sourced, not executed).
# Discovers nrUE pods on the ue cluster that have an oaitun_* PDU interface.

: "${SCRIPT_DIR:=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
UE_HOST="${UE_HOST:-usrp}"
OST_SERVER="${OST_SERVER:-http://10.1.132.11/}"
OST_HOST="${OST_HOST:-${OST_SERVER#http://}}"
OST_HOST="${OST_HOST#https://}"
OST_HOST="${OST_HOST%%/*}"
TUN_MTU="${TUN_MTU:-1350}"
KUBECONFIG_REMOTE="${KUBECONFIG_REMOTE:-/etc/kubernetes/admin.conf}"

ssh_ue() {
  ssh -F "$SSH_CONFIG" -o ConnectTimeout=15 -o RequestTTY=no "$UE_HOST" "$@"
}

kubectl_ue() {
  ssh_ue "sudo kubectl --kubeconfig=${KUBECONFIG_REMOTE} $(printf '%q ' "$@")"
}

# Print lines: id|namespace|pod|container|tun|ue_ip
# id is 1-based in discovery order.
discover_ues() {
  ssh_ue "sudo bash -s" <<'REMOTE'
set -euo pipefail
KCFG=/etc/kubernetes/admin.conf
id=0
# Prefer oai-* namespaces; fall back to all if none.
mapfile -t nss < <(kubectl --kubeconfig="$KCFG" get ns -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
  | grep -E '^oai-' || true)
if [[ ${#nss[@]} -eq 0 ]]; then
  mapfile -t nss < <(kubectl --kubeconfig="$KCFG" get ns -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
fi
for ns in "${nss[@]}"; do
  [[ "$ns" == kube-* || "$ns" == default || "$ns" == local-path-* || "$ns" == metallb-* ]] && continue
  while IFS= read -r pod; do
    [[ -z "$pod" ]] && continue
    # Skip non-Running
    phase="$(kubectl --kubeconfig="$KCFG" -n "$ns" get pod "$pod" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    [[ "$phase" == "Running" ]] || continue
    ctr="$(kubectl --kubeconfig="$KCFG" -n "$ns" get pod "$pod" -o jsonpath='{.spec.containers[0].name}' 2>/dev/null || true)"
    [[ -z "$ctr" ]] && continue
    info="$(kubectl --kubeconfig="$KCFG" -n "$ns" exec "$pod" -c "$ctr" -- sh -c '
      for t in /sys/class/net/oaitun_*; do
        [ -e "$t" ] || continue
        name=$(basename "$t")
        ip=$(ip -4 -o addr show dev "$name" 2>/dev/null | awk "{print \$4}" | cut -d/ -f1)
        [ -n "$ip" ] && echo "$name $ip" && exit 0
      done
      exit 1
    ' 2>/dev/null || true)"
    [[ -z "$info" ]] && continue
    tun="${info%% *}"
    ue_ip="${info##* }"
    id=$((id + 1))
    printf '%s|%s|%s|%s|%s|%s\n' "$id" "$ns" "$pod" "$ctr" "$tun" "$ue_ip"
  done < <(kubectl --kubeconfig="$KCFG" -n "$ns" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
done
REMOTE
}

resolve_ue() {
  local sel="${1:-}"
  local line id
  if [[ -z "$sel" ]]; then
    echo "error: UE id required (run list_ues.sh)" >&2
    return 1
  fi
  while IFS= read -r line; do
    id="${line%%|*}"
    if [[ "$sel" == "$id" || "$sel" == "$line" ]]; then
      printf '%s\n' "$line"
      return 0
    fi
    # Allow namespace/pod or pod name match
    IFS='|' read -r _ ns pod _ _ _ <<<"$line"
    if [[ "$sel" == "$ns/$pod" || "$sel" == "$pod" || "$sel" == "$ns" ]]; then
      printf '%s\n' "$line"
      return 0
    fi
  done < <(discover_ues)
  echo "error: UE '${sel}' not found" >&2
  return 1
}

prep_ue_path() {
  local ns="$1" pod="$2" ctr="$3" tun="$4" dest_ip="$5"
  ssh_ue "sudo kubectl --kubeconfig=${KUBECONFIG_REMOTE} -n $(printf '%q' "$ns") exec $(printf '%q' "$pod") -c $(printf '%q' "$ctr") -- sh -c $(printf '%q' "
    ip link set dev ${tun} mtu ${TUN_MTU} 2>/dev/null || true
    ip route replace ${dest_ip} dev ${tun} 2>/dev/null || ip route add ${dest_ip} dev ${tun} 2>/dev/null || true
  ")"
}

copy_speedtest_py() {
  local ns="$1" pod="$2" ctr="${3:-}"
  local local_py="$SCRIPT_DIR/speedtest.py"
  local remote_tmp="/tmp/speedtest.py.$$"
  local cp_args=()
  if [[ ! -f "$local_py" ]]; then
    echo "error: missing $local_py" >&2
    return 1
  fi
  scp -F "$SSH_CONFIG" -o ConnectTimeout=15 "$local_py" "${UE_HOST}:${remote_tmp}" >/dev/null
  if [[ -n "$ctr" ]]; then
    cp_args+=(-c "$ctr")
  fi
  ssh_ue "sudo kubectl --kubeconfig=${KUBECONFIG_REMOTE} -n $(printf '%q' "$ns") cp $(printf '%q' "$remote_tmp") $(printf '%q' "${pod}:/tmp/speedtest.py") $(printf '%q ' "${cp_args[@]}") && rm -f $(printf '%q' "$remote_tmp")"
}
