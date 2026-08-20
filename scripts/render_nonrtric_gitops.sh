#!/usr/bin/env bash
# ==============================================================================
# Render Non-RT-RIC (A1-PMS, ICS, DMaaP Adapter, rAppManager, CAPIF, ServiceManager, A1-Sim)
# GitOps manifests for edge cluster under repos/edge-repo/namespaces/neuro-ran-smo/nonrtric/
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${ROOT_DIR}/repos/edge-repo/namespaces/neuro-ran-smo/nonrtric"

export PATH="${HOME}/.local/bin:${PATH}"

mkdir -p "${OUTPUT_DIR}"

TMP_BUILD_DIR=$(mktemp -d /tmp/nonrtric_build.XXXXXX)
trap 'rm -rf "${TMP_BUILD_DIR}"' EXIT

TEMP_RAW="${TMP_BUILD_DIR}/nonrtric_raw.yaml"
echo "==> Rendering Non-RT-RIC Helm templates..."

# 1. Policy Management Service (A1-PMS)
helm template pms oran-release/policymanagementservice \
  --namespace nonrtric \
  --set persistence.storageClassName=local-path \
  > "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 2. Information Coordinator Service (ICS / DME)
helm template ics oran-release/informationservice \
  --namespace nonrtric \
  --set persistence.storageClassName=local-path \
  >> "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 3. DMaaP Adapter Service
helm template dmaap-adapter oran-release/dmaapadapterservice \
  --namespace nonrtric \
  >> "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 4. rApp Manager
helm template rappmgr oran-release/rappmanager \
  --namespace nonrtric \
  --set persistence.storageClassName=local-path \
  >> "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 5. CAPIF Core
helm template capif oran-release/capifcore \
  --namespace nonrtric \
  >> "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 6. Service Manager
helm template sm oran-release/servicemanager \
  --namespace nonrtric \
  >> "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 7. A1 Simulator
helm template a1-sim oran-release/a1simulator \
  --namespace nonrtric \
  >> "${TEMP_RAW}"

echo "==> Processing and injecting node affinity into Non-RT-RIC manifests..."
python3 - <<EOF
import yaml
import sys
import os

with open("${TEMP_RAW}", "r") as f:
    docs = list(yaml.safe_load_all(f))

affinity_patch = {
    "nodeAffinity": {
        "requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {
                    "matchExpressions": [
                        {
                            "key": "kubernetes.io/hostname",
                            "operator": "In",
                            "values": ["cpu-edge-0", "cpu-edge-1"]
                        }
                    ]
                }
            ]
        }
    }
}

namespace_doc = {
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {
        "name": "nonrtric",
        "labels": {
            "app.kubernetes.io/part-of": "neuro-ran-smo",
            "app.kubernetes.io/managed-by": "configsync"
        }
    }
}

rbac_secrets = []
services_net = []
pvcs = []
statefulsets = []
deployments = []

for doc in docs:
    if not doc:
        continue
    
    kind = doc.get("kind", "")
    if kind in ["KafkaUser", "KafkaTopic"]:
        continue
    
    meta = doc.setdefault("metadata", {})
    if kind in ["ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"]:
        meta.pop("namespace", None)
    else:
        if "namespace" not in meta:
            meta["namespace"] = "nonrtric"
    
    # Inject node affinity into Pod template spec
    if kind in ["Deployment", "StatefulSet", "DaemonSet"]:
        spec = doc.setdefault("spec", {})
        template = spec.setdefault("template", {})
        pod_spec = template.setdefault("spec", {})
        
        aff = pod_spec.setdefault("affinity", {})
        aff["nodeAffinity"] = affinity_patch["nodeAffinity"]
        
    if kind in ["ServiceAccount", "Secret", "ConfigMap", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"]:
        rbac_secrets.append(doc)
    elif kind in ["Service", "NetworkPolicy", "PodDisruptionBudget", "Ingress"]:
        services_net.append(doc)
    elif kind == "PersistentVolumeClaim":
        pvcs.append(doc)
    elif kind == "StatefulSet":
        statefulsets.append(doc)
    elif kind == "Deployment":
        deployments.append(doc)
    else:
        rbac_secrets.append(doc)

out_dir = "${OUTPUT_DIR}"

def write_docs(filename, doc_list):
    path = os.path.join(out_dir, filename)
    with open(path, "w") as f:
        yaml.dump_all(doc_list, f, sort_keys=False)
    print(f"Wrote {len(doc_list)} resources to {path}")

# Write organized manifests
write_docs("00-namespace.yaml", [namespace_doc])
write_docs("10-config-and-secrets.yaml", rbac_secrets)
write_docs("20-services-and-pvcs.yaml", services_net + pvcs)
write_docs("30-statefulsets.yaml", statefulsets)
write_docs("40-deployments.yaml", deployments)

print("==> All Non-RT-RIC GitOps manifests successfully generated!")
EOF
