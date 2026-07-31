#!/usr/bin/env bash
# List nrUE pods on the ue cluster that have an active PDU tunnel (oaitun_*).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ue_common.sh
source "$SCRIPT_DIR/ue_common.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0")

List UEs with a PDU session tunnel on ${UE_HOST}.

Columns: ID  NAMESPACE  POD  TUN  UE_IP

Use the ID with:
  ./speedtest.sh <id>
  ./pingtest.sh <id>

Environment:
  UE_HOST      SSH host with kubectl for edge UE pods (default: usrp)
  SSH_CONFIG   SSH config (default: utils/ssh_config/config)
EOF
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  if [[ ! -f "$SSH_CONFIG" ]]; then
    echo "error: SSH config not found: $SSH_CONFIG" >&2
    exit 1
  fi

  local lines=() line id ns pod ctr tun ue_ip
  mapfile -t lines < <(discover_ues)

  if [[ ${#lines[@]} -eq 0 ]]; then
    echo "No UEs with oaitun_* found on ${UE_HOST}."
    exit 1
  fi

  printf '%-4s %-40s %-55s %-12s %s\n' "ID" "NAMESPACE" "POD" "TUN" "UE_IP"
  printf '%-4s %-40s %-55s %-12s %s\n' "--" "---------" "---" "---" "-----"
  for line in "${lines[@]}"; do
    IFS='|' read -r id ns pod ctr tun ue_ip <<<"$line"
    printf '%-4s %-40s %-55s %-12s %s\n' "$id" "$ns" "$pod" "$tun" "$ue_ip"
  done
}

main "$@"
