#!/usr/bin/env bash
# Report Config Sync operator health and RootSync/RepoSync progress.
#
#   ./scripts/check-configsync.sh
#   ./scripts/check-configsync.sh -w 15
#   KCTX=central@central ./scripts/check-configsync.sh -n central-repo
set -euo pipefail

CTX="${KCTX:-mgmt@mgmt}"
NS="${CONFIGSYNC_NS:-config-management-system}"
ROOTSYNC_NAME="${ROOTSYNC_NAME:-mgmt}"
WATCH_INTERVAL=""
SHOW_ALL=0
VERBOSE=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Show Config Sync operator status and git sync progress.

Options:
  -c CONTEXT     kubectl context (default: ${CTX})
  -n NAME        RootSync name (default: ${ROOTSYNC_NAME}; use -a for all)
  -a             Show all RootSyncs and RepoSyncs in ${NS}
  -w [SECONDS]   Watch mode (default interval: 5)
  -v             Verbose: include full error lists when present
  -h             Help

Environment:
  KCTX              kubectl context
  CONFIGSYNC_NS     Config Sync namespace (default: config-management-system)
  ROOTSYNC_NAME     Default RootSync name (default: mgmt)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -c) CTX="$2"; shift 2 ;;
    -n) ROOTSYNC_NAME="$2"; shift 2 ;;
    -a) SHOW_ALL=1; shift ;;
    -w)
      if [[ "${2:-}" =~ ^[0-9]+$ ]]; then
        WATCH_INTERVAL="$2"
        shift 2
      else
        WATCH_INTERVAL=5
        shift
      fi
      ;;
    -v) VERBOSE=1; shift ;;
    -h) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

kubectl_ctx() {
  kubectl --context="$CTX" "$@"
}

jsonpath() {
  local obj="$1"
  local path="$2"
  kubectl_ctx get "$obj" -n "$NS" -o "jsonpath={$path}" 2>/dev/null || true
}

print_header() {
  echo "=== Config Sync @ ${CTX} ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ==="
  echo
}

print_operator() {
  echo "Operator pods (${NS}):"
  if ! kubectl_ctx get ns "$NS" >/dev/null 2>&1; then
    echo "  namespace ${NS} not found"
    echo
    return 1
  fi
  kubectl_ctx get pods -n "$NS" -o wide 2>/dev/null || echo "  (no pods)"
  echo
}

print_sync_resource() {
  local kind="$1"
  local name="$2"
  local resource="${kind}/${name}"

  if ! kubectl_ctx get "$resource" -n "$NS" >/dev/null 2>&1; then
    echo "${kind} ${name}: not found"
    echo
    return 0
  fi

  local repo branch period
  repo=$(jsonpath "$resource" '.spec.git.repo')
  branch=$(jsonpath "$resource" '.spec.git.branch')
  period=$(jsonpath "$resource" '.spec.git.period')

  local source_commit render_commit sync_commit last_synced
  source_commit=$(jsonpath "$resource" '.status.source.commit')
  render_commit=$(jsonpath "$resource" '.status.rendering.commit')
  sync_commit=$(jsonpath "$resource" '.status.sync.commit')
  last_synced=$(jsonpath "$resource" '.status.lastSyncedCommit')

  local source_err render_err sync_err
  source_err=$(jsonpath "$resource" '.status.source.errorSummary.totalCount')
  render_err=$(jsonpath "$resource" '.status.rendering.errorSummary.totalCount')
  sync_err=$(jsonpath "$resource" '.status.sync.errorSummary.totalCount')

  local source_up render_up sync_up
  source_up=$(jsonpath "$resource" '.status.source.lastUpdate')
  render_up=$(jsonpath "$resource" '.status.rendering.lastUpdate')
  sync_up=$(jsonpath "$resource" '.status.sync.lastUpdate')

  echo "${kind} ${name}:"
  echo "  repo:    ${repo:-n/a}"
  echo "  branch:  ${branch:-n/a}  period: ${period:-n/a}"
  echo "  commits: source=${source_commit:-pending}  render=${render_commit:-pending}  sync=${sync_commit:-pending}"
  echo "  lastSyncedCommit: ${last_synced:-pending}"
  echo "  errors:  source=${source_err:-0}  render=${render_err:-0}  sync=${sync_err:-0}"
  echo "  updated: source=${source_up:-n/a}  render=${render_up:-n/a}  sync=${sync_up:-n/a}"

  local stalled reconciling syncing
  stalled=$(jsonpath "$resource" '.status.conditions[?(@.type=="Stalled")].status')
  reconciling=$(jsonpath "$resource" '.status.conditions[?(@.type=="Reconciling")].status')
  syncing=$(jsonpath "$resource" '.status.conditions[?(@.type=="Syncing")].status')
  echo "  state:   Stalled=${stalled:-Unknown}  Reconciling=${reconciling:-Unknown}  Syncing=${syncing:-Unknown}"

  local sync_msg
  sync_msg=$(jsonpath "$resource" '.status.conditions[?(@.type=="Syncing")].message')
  [[ -n "$sync_msg" ]] && echo "  message: ${sync_msg}"

  if [[ "${source_commit:-}" == "${sync_commit:-}" && "${sync_commit:-}" == "${last_synced:-}" && \
        "${source_err:-0}" == "0" && "${render_err:-0}" == "0" && "${sync_err:-0}" == "0" && \
        "${syncing:-False}" == "False" && -n "${sync_commit:-}" ]]; then
    echo "  result:  OK (in sync with git)"
  elif [[ "${syncing:-}" == "True" ]]; then
    echo "  result:  SYNC IN PROGRESS"
  elif [[ "${source_err:-0}" != "0" || "${render_err:-0}" != "0" || "${sync_err:-0}" != "0" ]]; then
    echo "  result:  ERRORS (see below)"
  else
    echo "  result:  CHECK (commits or state may be settling)"
  fi

  if [[ "$VERBOSE" == "1" || "${source_err:-0}" != "0" || "${render_err:-0}" != "0" || "${sync_err:-0}" != "0" ]]; then
    for phase in source rendering sync; do
      local count
      count=$(jsonpath "$resource" ".status.${phase}.errorSummary.totalCount")
      [[ "${count:-0}" == "0" ]] && continue
      echo "  ${phase} errors:"
      kubectl_ctx get "$resource" -n "$NS" -o jsonpath="{range .status.${phase}.errorSummary.errorCountByCode[*]}{.errorCode}: {.count}{'\n'}{end}" 2>/dev/null || true
      if [[ "$VERBOSE" == "1" ]]; then
        kubectl_ctx get "$resource" -n "$NS" -o jsonpath="{range .status.${phase}.errors[*]}{.code}: {.errorMessage}{'\n'}{end}" 2>/dev/null || true
      fi
    done
  fi
  echo
}

print_syncs() {
  echo "RootSync / RepoSync summary:"
  kubectl_ctx get rootsyncs,reposyncs -n "$NS" -o wide 2>/dev/null || echo "  (none)"
  echo

  if [[ "$SHOW_ALL" == "1" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      kind="${line%%/*}"
      name="${line#*/}"
      print_sync_resource "$kind" "$name"
    done < <(kubectl_ctx get rootsyncs,reposyncs -n "$NS" -o name 2>/dev/null || true)
  else
    if kubectl_ctx get "rootsync/${ROOTSYNC_NAME}" -n "$NS" >/dev/null 2>&1; then
      print_sync_resource "rootsync" "$ROOTSYNC_NAME"
    elif kubectl_ctx get "reposync/${ROOTSYNC_NAME}" -n "$NS" >/dev/null 2>&1; then
      print_sync_resource "reposync" "$ROOTSYNC_NAME"
    else
      echo "RootSync/RepoSync '${ROOTSYNC_NAME}' not found in ${NS}."
      echo "Use -a to list all sync objects, or -n NAME."
      echo
    fi
  fi
}

run_once() {
  print_header
  print_operator || true
  print_syncs
}

main() {
  if [[ -n "$WATCH_INTERVAL" ]]; then
    while true; do
      clear 2>/dev/null || true
      run_once
      echo "Watching every ${WATCH_INTERVAL}s (Ctrl-C to stop) ..."
      sleep "$WATCH_INTERVAL"
    done
  else
    run_once
  fi
}

main
