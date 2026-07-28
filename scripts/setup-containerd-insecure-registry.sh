#!/usr/bin/env bash
# Allow containerd/CRI to pull from the HTTPS registry on mgmt (once per node).
# Registry uses a self-signed cert; hosts.toml skips TLS verify.
#
#   sudo ./scripts/setup-containerd-insecure-registry.sh
#   sudo ./scripts/setup-containerd-insecure-registry.sh 10.1.132.30:5000
#
# Run on every cluster node that must pull images (e.g. mgmt-0/1, edge-0, ue-1).
# Handles both containerd config v2 (cri.grpc) and v3 (cri.v1.images), including
# stock Docker CE configs with imports = [].
set -euo pipefail

REGISTRY="${1:-10.1.132.30:5000}"
CERTS_DIR="/etc/containerd/certs.d/${REGISTRY}"
CONF_D="/etc/containerd/conf.d"
CONFIG_TOML="/etc/containerd/config.toml"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0 [REGISTRY]" >&2
  exit 1
fi

mkdir -p "$CERTS_DIR" "$CONF_D"

# Valid TOML — unquoted host keys / capability strings break skip_verify (seen on mgmt-1).
cat >"${CERTS_DIR}/hosts.toml" <<EOF
server = "https://${REGISTRY}"

[host."https://${REGISTRY}"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
EOF
echo "Wrote ${CERTS_DIR}/hosts.toml"

# Drop-ins for both CRI registry plugin layouts (v2 + v3). Harmless if unused.
cat >"${CONF_D}/registry-certs.d.toml" <<'EOF'
[plugins.'io.containerd.cri.v1.images'.registry]
  config_path = "/etc/containerd/certs.d"

[plugins."io.containerd.grpc.v1.cri".registry]
  config_path = "/etc/containerd/certs.d"
EOF
echo "Wrote ${CONF_D}/registry-certs.d.toml"

ensure_conf_d_imports() {
  local tmp
  [[ -f "$CONFIG_TOML" ]] || return 0

  if grep -qE "imports\s*=\s*\[.*/etc/containerd/conf\.d" "$CONFIG_TOML"; then
    return 0
  fi

  # Empty imports = [] (common from containerd config dump / Docker CE).
  if grep -qE '^\s*imports\s*=\s*\[\s*\]\s*$' "$CONFIG_TOML"; then
    sed -i "s|^[[:space:]]*imports[[:space:]]*=[[:space:]]*\\[\\][[:space:]]*$|imports = ['/etc/containerd/conf.d/*.toml']|" "$CONFIG_TOML"
    echo "Set imports to conf.d in $CONFIG_TOML"
    return 0
  fi

  if grep -qE '^\s*imports\s*=' "$CONFIG_TOML"; then
    # Non-empty imports without conf.d — replace the line so drop-ins load.
    sed -i "s|^[[:space:]]*imports[[:space:]]*=.*$|imports = ['/etc/containerd/conf.d/*.toml']|" "$CONFIG_TOML"
    echo "Replaced imports with conf.d in $CONFIG_TOML"
    return 0
  fi

  tmp="$(mktemp)"
  if head -1 "$CONFIG_TOML" | grep -qE '^\s*version\s*='; then
    { head -1 "$CONFIG_TOML"; echo "imports = ['\''/etc/containerd/conf.d/*.toml'\'']"; tail -n +2 "$CONFIG_TOML"; } >"$tmp"
  else
    { echo "imports = ['\''/etc/containerd/conf.d/*.toml'\'']"; cat "$CONFIG_TOML"; } >"$tmp"
  fi
  mv "$tmp" "$CONFIG_TOML"
  echo "Added imports for conf.d to $CONFIG_TOML"
}

set_registry_config_path() {
  local path="$1"
  python3 - "$path" <<'PY' || true
import re, sys
path = sys.argv[1]
orig = open(path).read()
text = orig
for sec in (
    r"\[plugins\.'io\.containerd\.cri\.v1\.images'\.registry\]",
    r'\[plugins\."io\.containerd\.grpc\.v1\.cri"\.registry\]',
):
    pat = re.compile(rf"({sec}\s*\n)(.*?)(?=\n\s*\[|\Z)", re.S)

    def repl(m):
        head, body = m.group(1), m.group(2)
        if re.search(r"^\s*config_path\s*=", body, re.M):
            body = re.sub(
                r"^\s*config_path\s*=\s*.*$",
                '      config_path = "/etc/containerd/certs.d"',
                body,
                count=1,
                flags=re.M,
            )
        else:
            body = '      config_path = "/etc/containerd/certs.d"\n' + body
        return head + body

    text = pat.sub(repl, text, count=1)

text = re.sub(
    r'^(\s*config_path\s*=\s*)(?:""|' + "''" + r')\s*$',
    r'\1"/etc/containerd/certs.d"',
    text,
    flags=re.M,
)
if text != orig:
    open(path, "w").write(text)
    print(f"Set registry config_path in {path}")
PY
}

if [[ -f "$CONFIG_TOML" ]]; then
  ensure_conf_d_imports
  set_registry_config_path "$CONFIG_TOML"
fi

systemctl restart containerd
echo "containerd restarted."
