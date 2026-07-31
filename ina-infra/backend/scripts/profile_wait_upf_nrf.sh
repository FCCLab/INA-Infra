#!/usr/bin/env bash
# Sync UPF Deployment initContainers NRF wait URL, then wait for UPF→NRF.
#
# OAI UPF controller bakes nrf_svc into the Deployment only at create time.
# When Apply changes the profile subnet (e.g. .140 → .141), ConfigMaps update
# but existing Deployments keep the old init curl target and hang forever.
#
# Default: sync init URL + wait for UPF→NRF registration.
#
# Usage:
#   ./backend/scripts/profile_wait_upf_nrf.sh <profilename>
#   ./backend/scripts/profile_wait_upf_nrf.sh test --nrf 10.1.141.11
#   ./backend/scripts/profile_wait_upf_nrf.sh ina-infra --sync-only   # patch only
#   ./backend/scripts/profile_wait_upf_nrf.sh ina-infra --wait-only   # wait only
#
# Env: INA_SMF_CONTEXT (default central@central) for reading ina-core-ips.
#      NRF_WAIT_SEC, NRF_LOG_SINCE, SLICE_COUNT (forwarded to wait_upf_nrf.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SMF_CTX="${INA_SMF_CONTEXT:-central@central}"
CONTEXTS=(central@central regional@regional edge@edge)
NRF_SBI=""
DRY_RUN=0
DO_SYNC=1
DO_WAIT=1
WAIT_TIMEOUT="${NRF_WAIT_SEC:-120}"
WAIT_SINCE="${NRF_LOG_SINCE:-5m}"
SLICES=()

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

if [[ $# -lt 1 ]]; then
  echo "error: missing <profilename>" >&2
  usage 1
fi

case "$1" in
  -h|--help) usage 0 ;;
  -*)
    echo "error: first argument must be <profilename>, got: $1" >&2
    usage 1
    ;;
esac

NS="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --nrf) NRF_SBI="${2:?}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --context) SMF_CTX="${2:?}"; shift 2 ;;
    --sync-only) DO_SYNC=1; DO_WAIT=0; shift ;;
    --wait-only) DO_SYNC=0; DO_WAIT=1; shift ;;
    --no-wait) DO_WAIT=0; shift ;;
    --no-sync) DO_SYNC=0; shift ;;
    --timeout) WAIT_TIMEOUT="${2:?}"; shift 2 ;;
    --since) WAIT_SINCE="${2:?}"; shift 2 ;;
    --slice)
      SLICES+=("${2:?}")
      shift 2
      ;;
    --slice1|--slice2|--slice3|--slice4)
      SLICES+=("${1#--slice}")
      shift
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ "$DO_SYNC" != "1" && "$DO_WAIT" != "1" ]]; then
  echo "error: nothing to do (--no-sync and --no-wait)" >&2
  exit 1
fi

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central:${HOME}/.kube/config-regional:${HOME}/.kube/config-edge"
fi

if [[ -z "$NRF_SBI" ]]; then
  NRF_SBI="$(
    kubectl --context "$SMF_CTX" -n "$NS" get cm ina-core-ips \
      -o jsonpath='{.data.nrf_sbi}' 2>/dev/null || true
  )"
fi
if [[ -z "$NRF_SBI" && "$DO_SYNC" == "1" ]]; then
  echo "error: cannot resolve nrf_sbi (pass --nrf or ensure ${NS}/ina-core-ips exists)" >&2
  exit 1
fi

init_cmd() {
  local nrf="$1"
  printf "until curl --connect-timeout 1 --head -X GET http://%s/nnrf-nfm/v1/nf-instances?nf-type='NRF' --http2-prior-knowledge; do echo waiting for nrf svc %s to respond; sleep 1; done" "$nrf" "$nrf"
}

sync_upf_init() {
  local WANT_CMD patched checked name ctx cur patch
  WANT_CMD="$(init_cmd "$NRF_SBI")"
  patched=0
  checked=0

  echo "Sync UPF init NRF wait → ${NRF_SBI} (ns=${NS})"

  for ctx in "${CONTEXTS[@]}"; do
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      checked=$((checked + 1))
      cur="$(
        kubectl --context "$ctx" -n "$NS" get "deploy/${name}" \
          -o jsonpath='{.spec.template.spec.initContainers[0].command[2]}' 2>/dev/null || true
      )"
      if [[ -z "$cur" ]]; then
        echo "  skip ${ctx}/${name}: no init container"
        continue
      fi
      if [[ "$cur" == "$WANT_CMD" ]]; then
        echo "  ok   ${ctx}/${name}"
        continue
      fi
      echo "  patch ${ctx}/${name}"
      echo "        was: ${cur}"
      if [[ "$DRY_RUN" == "1" ]]; then
        echo "        would set nrf=${NRF_SBI}"
        continue
      fi
      patch="$(
        python3 -c '
import json, sys
cmd = sys.argv[1]
print(json.dumps([{
    "op": "replace",
    "path": "/spec/template/spec/initContainers/0/command",
    "value": ["sh", "-c", cmd],
}]))
' "$WANT_CMD"
      )"
      kubectl --context "$ctx" -n "$NS" patch "deploy/${name}" --type=json -p "$patch" >/dev/null
      patched=$((patched + 1))
    done < <(
      kubectl --context "$ctx" -n "$NS" get deploy \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
        | grep -E '^upf-slice-[0-9]+$' || true
    )
  done

  if [[ "$checked" -eq 0 ]]; then
    echo "error: no deploy/upf-slice-* in ns=${NS}" >&2
    exit 1
  fi

  echo "RESULT: checked=${checked} patched=${patched} nrf_sbi=${NRF_SBI}"
}

wait_upf_nrf() {
  local -a args=(--ns "$NS" --timeout "$WAIT_TIMEOUT" --since "$WAIT_SINCE")
  local n
  for n in "${SLICES[@]+"${SLICES[@]}"}"; do
    args+=(--slice "$n")
  done
  echo "Wait UPF→NRF registration (ns=${NS} timeout=${WAIT_TIMEOUT}s since=${WAIT_SINCE})"
  NRF_WAIT_SEC="$WAIT_TIMEOUT" NRF_LOG_SINCE="$WAIT_SINCE" \
    "$SCRIPT_DIR/wait_upf_nrf.sh" "${args[@]}"
}

if [[ "$DO_SYNC" == "1" ]]; then
  sync_upf_init
fi

if [[ "$DO_WAIT" == "1" ]]; then
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "skip wait (dry-run)"
    exit 0
  fi
  wait_upf_nrf
fi
