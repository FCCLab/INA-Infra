#!/usr/bin/env bash
# Symlink ~/.ssh/config -> utils/ssh_config/config so host aliases work without -F.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"
DST="${HOME}/.ssh/config"

log() { printf '==> %s\n' "$*"; }
err() { printf 'error: %s\n' "$*" >&2; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [-y]

Symlink ${DST} -> utils/ssh_config/config so \`ssh edge-2\` (and other lab
aliases) resolve without \`ssh -F\`.

Options:
  -y, --yes   Replace existing ~/.ssh/config without prompting
  -h, --help  Show this help

Environment:
  SSH_CONFIG  Source config (default: utils/ssh_config/config)
EOF
}

main() {
  local assume_yes=0 ans

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -y|--yes) assume_yes=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) err "unknown arg: $1"; usage >&2; exit 1 ;;
    esac
  done

  if [[ ! -f "$SRC" ]]; then
    err "source not found: $SRC"
    exit 1
  fi

  mkdir -p "${HOME}/.ssh"
  chmod 700 "${HOME}/.ssh"

  if [[ -L "$DST" ]]; then
    local current
    current="$(readlink -f "$DST" 2>/dev/null || readlink "$DST")"
    if [[ "$current" == "$(readlink -f "$SRC")" ]]; then
      log "already linked: ${DST} -> ${SRC}"
      exit 0
    fi
    log "replacing symlink ${DST} (-> ${current})"
    rm -f "$DST"
  elif [[ -e "$DST" ]]; then
    if [[ "$assume_yes" != "1" ]]; then
      read -rp "Backup existing ${DST} to ${DST}.bak and replace? [y/N] " ans
      if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
        echo "Aborted."
        exit 1
      fi
    fi
    mv -f "$DST" "${DST}.bak"
    log "backed up ${DST} -> ${DST}.bak"
  fi

  ln -s "$SRC" "$DST"
  chmod 600 "$SRC" 2>/dev/null || true
  log "linked ${DST} -> ${SRC}"
  log "try: ssh edge-2"
}

main "$@"
