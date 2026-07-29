#!/usr/bin/env bash
# Patch live ina-infra MySQL: UDR-compatible AM/NSSAI + per-UE SessionManagement oaiN.
#
# OAI UDR v2.2.1 am-data lookup (debug-verified) uses:
#   SELECT ... WHERE ueid='00101' AND servingPlmnid=''
# i.e. PLMN prefix from IMSI, empty servingPlmnid — NOT full SUPI / '00101'.
#
# Idempotent updates for UE101–104 (slice 1–4). Does not recreate MySQL.
#
# Usage:
#   ./scripts/patch_ina_mysql_live.sh
#   INA_NS=ina-infra ./scripts/patch_ina_mysql_live.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

NS="${INA_NS:-${PROFILE_NS:-ina-infra}}"
CTX="${INA_MYSQL_CONTEXT:-central@central}"
MYSQL_POD="${MYSQL_POD:-deploy/mysql}"
MYSQL_USER="${MYSQL_USER:-test}"
MYSQL_PASS="${MYSQL_PASS:-test}"

if [[ -z "${KUBECONFIG:-}" ]]; then
  export KUBECONFIG="${HOME}/.kube/config:${HOME}/.kube/config-central"
fi

SQL_FILE="$(mktemp)"
trap 'rm -f "$SQL_FILE"' EXIT

python3 <<'PY' >"$SQL_FILE"
import json

PLMN = "00101"
# UDR am-data PK used by v2.2.1 (see udr_db debug: servingPlmnid='').
UDR_UEID = "00101"
UDR_SERVING = ""

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

# Remove wrong-format AM rows (full SUPI + servingPlmnid=00101, duplicate 00101/00101).
lines.append(
    "DELETE FROM AccessAndMobilitySubscriptionData "
    "WHERE ueid IN ('00101','001010000000101','001010000000102',"
    "'001010000000103','001010000000104');"
)

# One AM row for PLMN 00101 — all slice NSSAIs (UDR uses same key for all IMSIs 00101…).
nssai = json.dumps(
    {
        "defaultSingleNssais": [
            {"sst": 1, "sd": f"{n:06d}"} for n in range(1, 5)
        ]
    }
)
lines.append(
    "INSERT INTO AccessAndMobilitySubscriptionData "
    f"(ueid, servingPlmnid, nssai) VALUES ('{UDR_UEID}', '{UDR_SERVING}', '{nssai}');"
)

for n in range(1, 5):
    ue = f"00101000000010{n}"
    dnn = f"oai{n}"
    # SMF/UDM query uses sd \"1\" (not zero-padded); UDR returns null on mismatch.
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

echo "Live MySQL patched (ns=$NS ctx=$CTX)"
echo -n "  AM (UDR key): "
kubectl --context "$CTX" -n "$NS" exec "$MYSQL_POD" -c mysql -- \
  mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" -N -e \
  "USE oai_db; SELECT CONCAT(ueid,'/',servingPlmnid) FROM AccessAndMobilitySubscriptionData WHERE ueid='00101';" \
  2>/dev/null | tail -1
for n in 1 2 3 4; do
  ue="00101000000010${n}"
  echo -n "  UE${n} dnn: "
  kubectl --context "$CTX" -n "$NS" exec "$MYSQL_POD" -c mysql -- \
    mysql -u"$MYSQL_USER" -p"$MYSQL_PASS" -N -e \
    "USE oai_db; SELECT JSON_KEYS(dnnConfigurations) FROM SessionManagementSubscriptionData WHERE ueid='${ue}';" \
    2>/dev/null | tail -1
done
