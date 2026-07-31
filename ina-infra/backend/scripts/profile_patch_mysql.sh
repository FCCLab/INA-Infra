#!/usr/bin/env bash
# Patch live profile MySQL: AM defaultSingleNssais for N slices + SessionManagement oaiN.
#
# OAI UDR v2.2.1 am-data lookup uses:
#   SELECT ... WHERE ueid='00101' AND servingPlmnid=''
# AM NSSAI count must match the profile's AMF plmn_support_list (N from PL / Apply).
#
# Usage:
#   ./backend/scripts/profile_patch_mysql.sh <profilename>
#   ./backend/scripts/profile_patch_mysql.sh test --slices 1
#   ./backend/scripts/profile_patch_mysql.sh ina-infra
#
# Env: INA_MYSQL_CONTEXT (default central@central), MYSQL_*.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

CTX="${INA_MYSQL_CONTEXT:-central@central}"
MYSQL_POD="${MYSQL_POD:-deploy/mysql}"
MYSQL_USER="${MYSQL_USER:-test}"
MYSQL_PASS="${MYSQL_PASS:-test}"
N_SLICES=""

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
    --slices) N_SLICES="${2:?}"; shift 2 ;;
    --context) CTX="${2:?}"; shift 2 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage 1
      ;;
  esac
done

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central"
fi

if [[ -z "$N_SLICES" ]]; then
  N_SLICES="$(
    kubectl --context "$CTX" -n "$NS" get cm ina-pl-placement \
      -o jsonpath='{.data.placement\.json}' 2>/dev/null \
      | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(0)
d = json.loads(raw)
n = (d.get("ip_plan") or {}).get("n_slices")
if n is None:
    n = len(d.get("slices") or d.get("deploy_map") or {})
if int(n or 0) > 0:
    print(int(n))
' 2>/dev/null || true
  )"
fi
if [[ -z "$N_SLICES" ]]; then
  N_SLICES="$(
    kubectl --context "$CTX" -n "$NS" get cm \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
      | grep -cE '^ina-slice-[0-9]+-ips$' || true
  )"
fi
if [[ -z "$N_SLICES" || "$N_SLICES" -lt 1 ]]; then
  echo "error: cannot resolve slice count for ns=$NS (pass --slices N)" >&2
  exit 1
fi

echo "Patch MySQL AM/SM for profile=${NS} N=${N_SLICES} (${CTX})"

SQL_FILE="$(mktemp)"
trap 'rm -f "$SQL_FILE"' EXIT

N_SLICES="$N_SLICES" python3 - <<'PY' >"$SQL_FILE"
import json
import os

PLMN = "00101"
UDR_UEID = "00101"
UDR_SERVING = ""
N = max(int(os.environ["N_SLICES"]), 1)

def dnn_conf(dnn: str) -> str:
    o = {
        dnn: {
            "pduSessionTypes": {"defaultSessionType": "IPV4"},
            "sscModes": {"defaultSscMode": "SSC_MODE_1"},
            "5gQosProfile": {
                "5qi": 1,
                "arp": {
                    "priorityLevel": 15,
                    "preemptCap": "NOT_PREEMPT",
                    "preemptVuln": "PREEMPTABLE",
                },
                "priorityLevel": 1,
            },
            "sessionAmbr": {"uplink": "1000Mbps", "downlink": "1000Mbps"},
        },
        "ims": {
            "pduSessionTypes": {"defaultSessionType": "IPV4V6"},
            "sscModes": {"defaultSscMode": "SSC_MODE_1"},
            "5gQosProfile": {
                "5qi": 2,
                "arp": {
                    "priorityLevel": 15,
                    "preemptCap": "NOT_PREEMPT",
                    "preemptVuln": "PREEMPTABLE",
                },
                "priorityLevel": 1,
            },
            "sessionAmbr": {"uplink": "1000Mbps", "downlink": "1000Mbps"},
        },
    }
    return json.dumps(o, separators=(",", ": ")).replace("'", "''")

lines = ["USE oai_db;", "START TRANSACTION;"]

# Drop PLMN AM row + any full-SUPI AM leftovers for UE101..10N.
ue_list = [f"00101000000010{n}" for n in range(1, N + 1)]
ue_sql = ",".join(f"'{u}'" for u in ["00101", *ue_list])
lines.append(
    f"DELETE FROM AccessAndMobilitySubscriptionData WHERE ueid IN ({ue_sql});"
)

nssai = json.dumps(
    {"defaultSingleNssais": [{"sst": 1, "sd": f"{n:06d}"} for n in range(1, N + 1)]}
)
lines.append(
    "INSERT INTO AccessAndMobilitySubscriptionData "
    f"(ueid, servingPlmnid, nssai) VALUES ('{UDR_UEID}', '{UDR_SERVING}', '{nssai}');"
)

for n in range(1, N + 1):
    ue = f"00101000000010{n}"
    dnn = f"oai{n}"
    # SMF/UDM query uses sd "1" (not zero-padded); UDR returns null on mismatch.
    sd = str(n)
    conf = dnn_conf(dnn)
    lines.append(
        "UPDATE SessionManagementSubscriptionData "
        f"SET singleNssai='{{\"sst\": 1, \"sd\": \"{sd}\"}}', "
        f"dnnConfigurations='{conf}' "
        f"WHERE ueid='{ue}' AND servingPlmnid='{PLMN}';"
    )
    lines.append(
        "INSERT INTO SessionManagementSubscriptionData "
        f"(ueid, servingPlmnid, singleNssai, dnnConfigurations) "
        f"SELECT '{ue}', '{PLMN}', '{{\"sst\": 1, \"sd\": \"{sd}\"}}', '{conf}' "
        f"FROM DUAL WHERE NOT EXISTS ("
        f"SELECT 1 FROM SessionManagementSubscriptionData "
        f"WHERE ueid='{ue}' AND servingPlmnid='{PLMN}');"
    )

lines.append("DELETE FROM SmfRegistrations WHERE ueid LIKE '00101000000010%';")
lines.append("COMMIT;")
print("\n".join(lines))
PY

kubectl --context "$CTX" -n "$NS" exec -i "$MYSQL_POD" -c mysql -- \
  mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" <"$SQL_FILE" 2>&1 | grep -v 'Using a password' || true

echo "Live MySQL patched (ns=$NS N=$N_SLICES ctx=$CTX)"
echo -n "  AM nssai: "
kubectl --context "$CTX" -n "$NS" exec "$MYSQL_POD" -c mysql -- \
  mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" -N -e \
  "USE oai_db; SELECT nssai FROM AccessAndMobilitySubscriptionData WHERE ueid='00101' AND servingPlmnid='';" \
  2>/dev/null | tail -1
for n in $(seq 1 "$N_SLICES"); do
  ue="00101000000010${n}"
  echo -n "  UE${n} dnn: "
  kubectl --context "$CTX" -n "$NS" exec "$MYSQL_POD" -c mysql -- \
    mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" -N -e \
    "USE oai_db; SELECT JSON_KEYS(dnnConfigurations) FROM SessionManagementSubscriptionData WHERE ueid='${ue}';" \
    2>/dev/null | tail -1
done
