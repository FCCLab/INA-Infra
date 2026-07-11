#!/usr/bin/env bash
# Ping a target from a UE PDU tunnel (oaitun_*).
# Default: continuous ping (Ctrl+C to stop). Use --count N for a fixed count.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ue_common.sh
source "$SCRIPT_DIR/ue_common.sh"

# Empty / 0 / unlimited => continuous ping (no -c).
COUNT="${COUNT:-}"
TARGET_DEFAULT="${TARGET_DEFAULT:-10.1.132.11}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <ue-id|namespace|pod> [target] [options]

List UEs first:
  ./list_ues.sh

Examples:
  $(basename "$0") 1                 # continuous ping (Ctrl+C to stop)
  $(basename "$0") 1 10.1.132.11
  $(basename "$0") 2 --count 5
  $(basename "$0") oai-nws-1ue --target 10.1.132.11

Options:
  --target IP     Ping destination (default: ${TARGET_DEFAULT})
  --count N       ICMP echo count (default: unlimited)
  -h, --help      Show help

Environment:
  UE_HOST TARGET_DEFAULT COUNT TUN_MTU SSH_CONFIG
EOF
}

main() {
  local ue_sel="" target="$TARGET_DEFAULT" count="$COUNT"
  local line id ns pod ctr tun ue_ip positional=()
  local count_args=() count_label="unlimited" ping_cmd

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) usage; exit 0 ;;
      --target|-t) target="$2"; shift 2 ;;
      --count|-c) count="$2"; shift 2 ;;
      -*)
        echo "error: unknown option $1" >&2
        usage >&2
        exit 1
        ;;
      *)
        positional+=("$1")
        shift
        ;;
    esac
  done

  if [[ ${#positional[@]} -ge 1 ]]; then
    ue_sel="${positional[0]}"
  fi
  if [[ ${#positional[@]} -ge 2 ]]; then
    target="${positional[1]}"
  fi
  if [[ ${#positional[@]} -gt 2 ]]; then
    echo "error: unexpected arguments: ${positional[*]:2}" >&2
    exit 1
  fi

  if [[ -z "$ue_sel" ]]; then
    echo "Available UEs:"
    "$SCRIPT_DIR/list_ues.sh" || true
    echo
    usage >&2
    exit 1
  fi

  if [[ -n "$count" && "$count" != "0" && "$count" != "unlimited" ]]; then
    count_args=(-c "$count")
    count_label="$count"
  fi

  line="$(resolve_ue "$ue_sel")"
  IFS='|' read -r id ns pod ctr tun ue_ip <<<"$line"

  echo "==> UE #${id}  ${ns}/${pod}"
  echo "    tun=${tun}  ue_ip=${ue_ip}  target=${target}  count=${count_label}"
  echo "==> prepare path (mtu ${TUN_MTU}, route ${target} via ${tun})"
  prep_ue_path "$ns" "$pod" "$ctr" "$tun" "$target"

  echo "==> ping (Ctrl+C to stop)"
  ping_cmd=(ping -W 2 -I "$tun")
  if [[ ${#count_args[@]} -gt 0 ]]; then
    ping_cmd+=("${count_args[@]}")
  fi
  ping_cmd+=("$target")

  # Continuous ping needs a TTY so Ctrl+C reaches ping through SSH/kubectl.
  if [[ ${#count_args[@]} -eq 0 ]]; then
    ssh -F "$SSH_CONFIG" -o ConnectTimeout=15 -tt "$UE_HOST" \
      "sudo kubectl --kubeconfig=${KUBECONFIG_REMOTE} -n $(printf '%q' "$ns") exec -it \
        $(printf '%q' "$pod") -c $(printf '%q' "$ctr") -- $(printf '%q ' "${ping_cmd[@]}")"
  else
    ssh_ue "sudo kubectl --kubeconfig=${KUBECONFIG_REMOTE} -n $(printf '%q' "$ns") exec \
      $(printf '%q' "$pod") -c $(printf '%q' "$ctr") -- $(printf '%q ' "${ping_cmd[@]}")"
  fi
}

main "$@"
