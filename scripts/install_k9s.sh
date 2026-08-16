#!/usr/bin/env bash
# Install k9s (Kubernetes terminal UI) locally and/or on testbed hosts via SSH.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SSH_CONFIG="${SSH_CONFIG:-$REPO_ROOT/utils/ssh_config/config}"

K9S_VERSION="${K9S_VERSION:-v0.51.0}"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/.local/bin}"
DOWNLOAD_CACHE_DIR=""

cleanup() {
  if [[ -n "$DOWNLOAD_CACHE_DIR" ]]; then
    rm -rf "$DOWNLOAD_CACHE_DIR"
  fi
}
trap cleanup EXIT

ALL_HOSTS=(
  mgmt-0 mgmt-1
  cpu-central-0 cpu-central-1
  cpu-regional-0 cpu-regional-1
  cpu-edge-0 cpu-edge-1
)

usage() {
  cat <<EOF
Usage: $(basename "$0") [host ...]

Install k9s ${K9S_VERSION} to ${INSTALL_DIR} on this machine and on testbed hosts.
With no arguments, installs locally and on all nodes (mgmt, central, regional, edge).

Examples:
  $(basename "$0")
  $(basename "$0") central-0 regional-0

On control-plane nodes after bringup:
  k9s --context central@central

From mgmt (all contexts; tunnels .137 APIs):
  ./scripts/k9s_mgmt.sh

Environment:
  SSH_CONFIG    SSH config (default: utils/ssh_config/config)
  K9S_VERSION   Release tag (default: v0.51.0)
  INSTALL_DIR   Install directory (default: ~/.local/bin)
  LOCAL_ONLY    Set to 1 to skip remote hosts
  REMOTE_ONLY   Set to 1 to skip local install
EOF
}

platform_from_uname() {
  case "${1:-}" in
    Linux) OS=Linux ;;
    Darwin) OS=Darwin ;;
    *)
      echo "error: unsupported OS: ${1:-}" >&2
      return 1
      ;;
  esac

  case "${2:-}" in
    x86_64 | amd64) ARCH=amd64 ;;
    aarch64 | arm64) ARCH=arm64 ;;
    *)
      echo "error: unsupported architecture: ${2:-}" >&2
      return 1
      ;;
  esac
}

k9s_installed_version() {
  command -v k9s >/dev/null 2>&1 || return 0
  k9s version -s 2>/dev/null | awk '/^Version/ { print $2; exit }'
}

download_k9s() {
  local os="$1" arch="$2"
  local url archive cache_file tmpdir

  cache_file="${DOWNLOAD_CACHE_DIR}/k9s_${os}_${arch}"
  if [[ -f "$cache_file" ]]; then
    printf '%s' "$cache_file"
    return 0
  fi

  if [[ -z "$DOWNLOAD_CACHE_DIR" ]]; then
    DOWNLOAD_CACHE_DIR="$(mktemp -d)"
  fi
  cache_file="${DOWNLOAD_CACHE_DIR}/k9s_${os}_${arch}"

  url="https://github.com/derailed/k9s/releases/download/${K9S_VERSION}/k9s_${os}_${arch}.tar.gz"
  archive="k9s_${os}_${arch}.tar.gz"
  tmpdir="$(mktemp -d)"

  echo "==> Download ${url}" >&2
  curl -fsSL "$url" -o "${tmpdir}/${archive}"
  tar -xzf "${tmpdir}/${archive}" -C "$tmpdir" k9s
  install -m 755 "${tmpdir}/k9s" "$cache_file"
  rm -rf "$tmpdir"
  printf '%s' "$cache_file"
}

install_k9s_binary() {
  local binary="$1" target_dir="$2"
  local dest

  mkdir -p "$target_dir"
  dest="${target_dir}/k9s"
  install -m 755 "$binary" "$dest"
  echo "==> Installed ${dest}" >&2
  "$dest" version -s
}

install_k9s_local() {
  local os arch current binary

  platform_from_uname "$(uname -s)" "$(uname -m)"

  current="$(k9s_installed_version)"
  if [[ "$current" == "$K9S_VERSION" ]]; then
    echo "Local: k9s ${K9S_VERSION} already installed: $(command -v k9s)"
    return 0
  fi
  if [[ -n "$current" ]]; then
    echo "Local: upgrading k9s ${current} -> ${K9S_VERSION}"
  else
    echo "Local: installing k9s ${K9S_VERSION}"
  fi

  binary="$(download_k9s "$OS" "$ARCH")"
  install_k9s_binary "$binary" "$INSTALL_DIR"

  case ":${PATH}:" in
    *":${INSTALL_DIR}:"*) ;;
    *)
      echo ""
      echo "Add ${INSTALL_DIR} to PATH, e.g.:"
      echo "  export PATH=\"${INSTALL_DIR}:\$PATH\""
      ;;
  esac
}

install_k9s_remote() {
  local host="$1"
  local os arch current remote_install_dir remote_binary local_binary

  echo ""
  echo ">>> ${host}"

  read -r os arch < <(ssh -F "$SSH_CONFIG" -o RequestTTY=no "$host" \
    'printf "%s %s\n" "$(uname -s)" "$(uname -m)"')

  current="$(ssh -F "$SSH_CONFIG" -o RequestTTY=no "$host" \
    'k9s_bin="${HOME}/.local/bin/k9s"; if [[ -x "$k9s_bin" ]]; then "$k9s_bin" version -s 2>/dev/null | awk "/^Version/ { print \$2; exit }"; fi')"
  if [[ "$current" == "$K9S_VERSION" ]]; then
    echo "    k9s ${K9S_VERSION} already installed"
    return 0
  fi
  if [[ -n "$current" ]]; then
    echo "    upgrading k9s ${current} -> ${K9S_VERSION}"
  else
    echo "    installing k9s ${K9S_VERSION}"
  fi

  platform_from_uname "$os" "$arch"
  local_binary="$(download_k9s "$OS" "$ARCH")"

  remote_binary="/tmp/k9s-install-${$}-${RANDOM}"

  scp -q -F "$SSH_CONFIG" "$local_binary" "${host}:${remote_binary}"
  ssh -F "$SSH_CONFIG" -o RequestTTY=no "$host" bash -s "$remote_binary" <<'EOF'
set -euo pipefail
binary="$1"
install_dir="${HOME}/.local/bin"
mkdir -p "$install_dir"
install -m 755 "$binary" "${install_dir}/k9s"
rm -f "$binary"
"${install_dir}/k9s" version -s
EOF
  echo "    installed: ~/.local/bin/k9s (add ~/.local/bin to PATH if needed)"
  return 0
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

hosts=()
if [[ $# -gt 0 ]]; then
  hosts=("$@")
elif [[ "${LOCAL_ONLY:-}" != "1" ]]; then
  hosts=("${ALL_HOSTS[@]}")
fi

if [[ "${REMOTE_ONLY:-}" != "1" ]]; then
  install_k9s_local
fi

failed=0
if [[ ${#hosts[@]} -gt 0 ]]; then
  if [[ ! -f "$SSH_CONFIG" ]]; then
    echo "error: SSH config not found: $SSH_CONFIG" >&2
    exit 1
  fi

  for host in "${hosts[@]}"; do
    if ! install_k9s_remote "$host"; then
      failed=1
    fi
  done
fi

exit "$failed"
