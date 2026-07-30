#!/usr/bin/env bash
# Render oai-gnb-ns-1ue nrUE (RFsim client) into repos/ for Config Sync on the ue cluster.
# Reuses the already-running mono gNB on edge/usrp (10.1.139.113:4043).
# UICC from INA-Infra-oai-slice-implementation/nws/nrue1.uicc.yaml; RF matches running gNB (51 PRB).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
OAI_NS_1UE_NS="${OAI_NS_1UE_NS:-oai-gnb-ns-1ue}"
OAI_NR_UE_IMAGE="${OAI_NR_UE_IMAGE:-10.1.132.30:5000/oai-nr-ue:nws-v0.2}"
UE_CLUSTERS=(ue)
# Running mono gNB F1 / rfsim (edge offset 3 → 10.1.139.113)
EDGE_GNB_RF_IP="${EDGE_GNB_RF_IP:-$(oai_macvlan_ip edge 3)}"
# Avoid clash with legacy oai-ue at offset 0 (.160)
UE_RF_OFFSET="${UE_RF_OFFSET:-2}"
UE_IMSI="${UE_IMSI:-001010000000001}"
UE_KEY="${UE_KEY:-fec86ba6eb707ed08905757b1bb44b8f}"
UE_OPC="${UE_OPC:-C42449363BBAD02B66D16BC975D77CC1}"
# Core SMF advertises sst=1/sd=ffffff + DNN internet (compose nrue1 used oai/0x000001).
UE_DNN="${UE_DNN:-internet}"
UE_NSSAI_SST="${UE_NSSAI_SST:-1}"
UE_NSSAI_SD="${UE_NSSAI_SD:-0xFFFFFF}"

write_ns_1ue_gitops() {
  local cluster="$1"
  local repo_name dest_ue
  local ue_rf_ip

  repo_name="$(cluster_gitea_repo_name "$cluster")"
  dest_ue="${REPOS_DIR}/${repo_name}/namespaces/${OAI_NS_1UE_NS}"
  mkdir -p "$dest_ue"

  ue_rf_ip="$(oai_macvlan_ip "$cluster" "$UE_RF_OFFSET")"

  python3 - "$dest_ue" "$OAI_NS_1UE_NS" "$OAI_NR_UE_IMAGE" "$SITE_IFACE" \
    "$ue_rf_ip" "$OAI_MACVLAN_GW" "$EDGE_GNB_RF_IP" \
    "$UE_IMSI" "$UE_KEY" "$UE_OPC" "$UE_DNN" "$UE_NSSAI_SST" "$UE_NSSAI_SD" <<'PY'
import json
import sys
from pathlib import Path

import yaml

(dest_ue, ue_ns, ue_image, nad_parent, ue_rf_ip, oai_gw, gnb_rf_ip,
 imsi, key, opc, dnn, nssai_sst, nssai_sd) = sys.argv[1:14]
dest_ue = Path(dest_ue)

prefixes = (
    "namespace-", "networkattachmentdefinition-", "configmap-",
    "serviceaccount-", "deployment-",
)
for old in dest_ue.glob("*.yaml"):
    if any(old.name.startswith(p) for p in prefixes):
        old.unlink()


def write_doc(doc, prefix=""):
    kind = doc["kind"].lower()
    name = doc["metadata"]["name"]
    (dest_ue / f"{prefix}{kind}-{name}.yaml").write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    )


write_doc({
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {"name": ue_ns},
})

nad_name = "ue-sim-rf"
nad_config = {
    "cniVersion": "0.3.1",
    "name": nad_name,
    "plugins": [
        {
            "type": "macvlan",
            "capabilities": {"ips": True},
            "master": nad_parent,
            "mode": "bridge",
            "ipam": {
                "type": "static",
                "addresses": [{"address": f"{ue_rf_ip}/24", "gateway": oai_gw}],
            },
        },
        {"type": "tuning", "capabilities": {"mac": True}, "ipam": {}},
    ],
}
write_doc({
    "apiVersion": "k8s.cni.cncf.io/v1",
    "kind": "NetworkAttachmentDefinition",
    "metadata": {"name": nad_name, "namespace": ue_ns},
    "spec": {"config": json.dumps(nad_config)},
}, prefix="10-")

# Libconfig form for nws-v0.2 entrypoint (same mount path as oai-ue).
ue_conf = f"""uicc0 = {{
  imsi = "{imsi}";
  key = "{key}";
  opc = "{opc}";
  dnn = "{dnn}";
  nssai_sst = {nssai_sst};
  nssai_sd = {nssai_sd};
}}

position0 = {{
  x = 0.0;
  y = 0.0;
  z = 6377900.0;
}}
"""
write_doc({
    "apiVersion": "v1",
    "kind": "ConfigMap",
    "metadata": {"name": "oai-ue-configmap", "namespace": ue_ns},
    "data": {"ue.conf": ue_conf},
}, prefix="11-")

write_doc({
    "apiVersion": "v1",
    "kind": "ServiceAccount",
    "metadata": {"name": "oai-ue-sa", "namespace": ue_ns},
}, prefix="12-")

networks_json = (
    "[\n {\n"
    f'  "name": "{nad_name}",\n'
    '  "interface": "rf",\n'
    f'  "ips": [{json.dumps(f"{ue_rf_ip}/24")}],\n'
    f'  "gateways": [{json.dumps(oai_gw)}]\n'
    " }\n]"
)
# RF matches running mono gNB (51 PRB / 3609120000), not compose 106 PRB.
additional_options = (
    f"--rfsim --log_config.global_log_options level,nocolor,time "
    f"--rfsimulator.serveraddr {gnb_rf_ip} --rfsimulator.serverport 4043 "
    "-C 3609120000 -r 51 --numerology 1 --ssb 234 --band 78 "
    f"--uicc0.imsi {imsi}"
)

write_doc({
    "apiVersion": "apps/v1",
    "kind": "Deployment",
    "metadata": {
        "name": "oai-ue",
        "namespace": ue_ns,
        "labels": {"app.kubernetes.io/name": "oai-ue"},
    },
    "spec": {
        "replicas": 1,
        "strategy": {"type": "Recreate"},
        "selector": {"matchLabels": {"app.kubernetes.io/name": "oai-ue"}},
        "template": {
            "metadata": {
                "labels": {
                    "app": "oai-ue",
                    "app.kubernetes.io/name": "oai-ue",
                },
                "annotations": {"k8s.v1.cni.cncf.io/networks": networks_json},
            },
            "spec": {
                "serviceAccountName": "oai-ue-sa",
                "terminationGracePeriodSeconds": 5,
                "containers": [{
                    "name": "ue",
                    "image": ue_image,
                    "imagePullPolicy": "IfNotPresent",
                    "securityContext": {"privileged": True},
                    "env": [
                        {"name": "USE_ADDITIONAL_OPTIONS", "value": additional_options},
                        {"name": "TZ", "value": "Europe/Paris"},
                    ],
                    "resources": {
                        "requests": {"cpu": "1000m", "memory": "512Mi"},
                        "limits": {"cpu": "2000m", "memory": "1Gi"},
                    },
                    "volumeMounts": [{
                        "name": "configuration",
                        "mountPath": "/opt/oai-nr-ue/etc/nr-ue.conf",
                        "subPath": "ue.conf",
                    }],
                }],
                "volumes": [{
                    "name": "configuration",
                    "configMap": {"name": "oai-ue-configmap"},
                }],
            },
        },
    },
}, prefix="15-")

print(f"  namespaces/{ue_ns}: nrUE RFsim client IMSI {imsi} DNN {dnn}")
print(f"  UE RF macvlan {ue_rf_ip} -> edge gNB rfsim {gnb_rf_ip}:4043")
PY

  echo "==> [${cluster}] ${REPOS_DIR}/${repo_name} (OAI nrUE → ${OAI_NS_1UE_NS})"
}

main() {
  local clusters=("$@")

  if [[ ${#clusters[@]} -eq 0 ]]; then
    clusters=(ue)
  fi

  for cluster in "${clusters[@]}"; do
    local ok=0
    for allowed in "${UE_CLUSTERS[@]}"; do
      [[ "$cluster" == "$allowed" ]] && ok=1
    done
    if [[ "$ok" -ne 1 ]]; then
      echo "error: oai-gnb-ns-1ue deploys on ue cluster only, not '${cluster}'" >&2
      exit 1
    fi
  done

  if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
  fi

  for cluster in "${clusters[@]}"; do
    write_ns_1ue_gitops "$cluster"
  done

  echo
  echo "Namespace: ${OAI_NS_1UE_NS}"
  echo "Prerequisites: Multus (./scripts/render_multus_gitops.sh ue)"
  echo "Running edge gNB rfsim on ${EDGE_GNB_RF_IP}:4043 (oai-ran-gnb)"
  echo "Subscriber IMSI ${UE_IMSI} (DNN ${UE_DNN}, SD ${UE_NSSAI_SD}) must exist in central MySQL"
  echo
  echo "Push: ./bringup/03_push_to_git_repos/push_git_repos.sh ue"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<EOF
Usage: $(basename "$0") [cluster ...]

Write oai-gnb-ns-1ue nrUE (RFsim → existing edge gNB) to repos/ for Config Sync.
Default cluster: ue.

Environment:
  OAI_NR_UE_IMAGE  nrUE image (default: 10.1.132.30:5000/oai-nr-ue:nws-v0.2)
  EDGE_GNB_RF_IP   gNB rfsim target (default: $(oai_macvlan_ip edge 3))
  UE_RF_OFFSET     macvlan offset under ue base (default: 2 → 10.1.139.162)
  UE_IMSI          Subscriber IMSI (default: 001010000000001)
  REPOS_DIR        Output tree (default: repos/)
EOF
  exit 0
fi

main "$@"
