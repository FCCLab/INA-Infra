#!/usr/bin/env bash
# Allow containerd/CRI to pull from the HTTPS registry on mgmt (once per node).
# Registry uses a self-signed cert; hosts.toml skips TLS verify.
#
#   sudo ./scripts/setup-containerd-insecure-registry.sh
#   sudo ./scripts/setup-containerd-insecure-registry.sh 10.1.132.30:5000
#
# Run on every cluster node that must pull images (e.g. mgmt-0/1, edge-0, ue-1).
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

# Drop-in so imports pick up certs.d even if the main config has config_path = ''.
cat >"${CONF_D}/registry-certs.d.toml" <<'EOF'
[plugins.'io.containerd.cri.v1.images'.registry]
  config_path = "/etc/containerd/certs.d"
EOF
echo "Wrote ${CONF_D}/registry-certs.d.toml"

# Ensure main config imports conf.d and does not leave an empty config_path that
# wins over the drop-in on some containerd versions.
if [[ -f "$CONFIG_TOML" ]]; then
  if ! grep -qE "imports\s*=\s*\[.*/etc/containerd/conf\.d" "$CONFIG_TOML"; then
    if grep -qE '^\s*imports\s*=' "$CONFIG_TOML"; then
      echo "Warning: $CONFIG_TOML has imports but not conf.d — add '/etc/containerd/conf.d/*.toml' manually." >&2
    else
      # Prepend imports near the top (after version if present).
      tmp="$(mktemp)"
      if head -1 "$CONFIG_TOML" | grep -qE '^\s*version\s*='; then
        { head -1 "$CONFIG_TOML"; echo "imports = ['\''/etc/containerd/conf.d/*.toml'\'']"; tail -n +2 "$CONFIG_TOML"; } >"$tmp"
      else
        { echo "imports = ['\''/etc/containerd/conf.d/*.toml'\'']"; cat "$CONFIG_TOML"; } >"$tmp"
      fi
      mv "$tmp" "$CONFIG_TOML"
      echo "Added imports for conf.d to $CONFIG_TOML"
    fi
  fi

  # Prefer a single certs.d path in the main registry section when present.
  if grep -q "plugins.'io.containerd.cri.v1.images'.registry" "$CONFIG_TOML"; then
    python3 - "$CONFIG_TOML" <<'PY' || true
import re, sys
path = sys.argv[1]
text = open(path).read()
# Set config_path under the cri.v1.images.registry table only.
pat = re.compile(
    r"(\[plugins\.'io\.containerd\.cri\.v1\.images'\.registry\]\s*\n)(.*?)(?=\n\s*\[|\Z)",
    re.S,
)
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
new, n = pat.subn(repl, text, count=1)
if n:
    open(path, "w").write(new)
    print(f"Set registry config_path in {path}")
else:
    print(f"No cri.v1.images.registry section found in {path}", file=sys.stderr)
PY
  fi
fi

systemctl restart containerd
echo "containerd restarted."
