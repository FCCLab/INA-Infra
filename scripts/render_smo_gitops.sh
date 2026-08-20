#!/usr/bin/env bash
# ==============================================================================
# Render SMO (Topology Exposure & Inventory, Kafka, PostgreSQL) GitOps manifests
# for edge cluster under repos/edge-repo/namespaces/neuro-ran-smo/smo/
# (Reads read-only from ./smo without modifying any files in ./smo)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SRC_CHART_DIR="${ROOT_DIR}/smo/osc-non-rt-ric/smo-teiv/charts/smo/topology-exposure-inventory"
SRC_COMMON_DIR="${ROOT_DIR}/smo/osc-non-rt-ric/smo-teiv/charts/smo/smo-common"
OUTPUT_DIR="${ROOT_DIR}/repos/edge-repo/namespaces/neuro-ran-smo/smo"

export PATH="${HOME}/.local/bin:${PATH}"

mkdir -p "${OUTPUT_DIR}"

TMP_BUILD_DIR=$(mktemp -d /tmp/smo_chart_build.XXXXXX)
trap 'rm -rf "${TMP_BUILD_DIR}"' EXIT

echo "==> Preparing temporary build workspace at ${TMP_BUILD_DIR}..."
cp -r "${SRC_CHART_DIR}" "${TMP_BUILD_DIR}/topology-exposure-inventory"
mkdir -p "${TMP_BUILD_DIR}/topology-exposure-inventory/charts"

# Prepare dependencies inside /tmp
helm package "${SRC_COMMON_DIR}" -d "${TMP_BUILD_DIR}/topology-exposure-inventory/charts" >/dev/null
helm pull bitnami/postgresql --version 15.5.8 -d "${TMP_BUILD_DIR}/topology-exposure-inventory/charts" >/dev/null
helm pull bitnami/kafka --version 29.3.4 -d "${TMP_BUILD_DIR}/topology-exposure-inventory/charts" >/dev/null

TEMP_RAW="${TMP_BUILD_DIR}/smo_teiv_raw.yaml"
echo "==> Rendering TEIV / SMO Helm template..."
helm template oran-smo "${TMP_BUILD_DIR}/topology-exposure-inventory" \
  --namespace smo \
  --set postgresql.primary.persistence.storageClass=local-path \
  --set postgresql.image.registry=docker.io \
  --set postgresql.image.repository=bitnami/postgresql \
  --set postgresql.image.tag=latest \
  --set postgresql.volumePermissions.image.registry=docker.io \
  --set postgresql.volumePermissions.image.repository=bitnami/os-shell \
  --set postgresql.volumePermissions.image.tag=latest \
  --set postgresql.primary.volumePermissions.image.registry=docker.io \
  --set postgresql.primary.volumePermissions.image.repository=bitnami/os-shell \
  --set postgresql.primary.volumePermissions.image.tag=latest \
  --set postgresql.primary.resources.limits.memory=2Gi \
  --set postgresql.primary.resources.limits.cpu="2" \
  --set postgresql.primary.resources.requests.memory=512Mi \
  --set postgresql.primary.resources.requests.cpu=500m \
  --set postgresql.primary.readinessProbe.initialDelaySeconds=15 \
  --set postgresql.primary.livenessProbe.initialDelaySeconds=15 \
  --set kafka.controller.persistence.storageClass=local-path \
  --set kafka.image.registry=docker.io \
  --set kafka.image.repository=bitnamilegacy/kafka \
  --set kafka.image.tag=latest \
  --set kafka.volumePermissions.image.registry=docker.io \
  --set kafka.volumePermissions.image.repository=bitnami/os-shell \
  --set kafka.volumePermissions.image.tag=latest \
  --set kafka.resources.limits.memory=2Gi \
  --set kafka.resources.requests.memory=512Mi \
  --set kafka.readinessProbe.initialDelaySeconds=15 \
  --set kafka.livenessProbe.initialDelaySeconds=15 \
  --set topology-exposure.image.registry=nexus3.o-ran-sc.org:10002 \
  --set topology-exposure.image.tag=0.1.0 \
  --set topology-exposure.liveness.initialDelaySeconds=45 \
  --set topology-exposure.readiness.initialDelaySeconds=45 \
  --set topology-ingestion.image.registry=nexus3.o-ran-sc.org:10002 \
  --set topology-ingestion.image.tag=0.1.0 \
  --set topology-ingestion.liveness.initialDelaySeconds=45 \
  --set topology-ingestion.readiness.initialDelaySeconds=45 \
  > "${TEMP_RAW}"

echo "==> Processing and injecting node affinity into manifests..."
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
        "name": "smo",
        "labels": {
            "app.kubernetes.io/part-of": "neuro-ran-smo",
            "app.kubernetes.io/managed-by": "configsync"
        }
    }
}

rbac_secrets = []
services_net = []
statefulsets = []
deployments = []

for doc in docs:
    if not doc:
        continue
    
    # Ensure namespace is set
    meta = doc.setdefault("metadata", {})
    if "namespace" not in meta:
        meta["namespace"] = "smo"
    
    kind = doc.get("kind", "")
    
    # Inject node affinity into Pod template spec
    if kind in ["Deployment", "StatefulSet", "DaemonSet"]:
        spec = doc.setdefault("spec", {})
        template = spec.setdefault("template", {})
        pod_spec = template.setdefault("spec", {})
        
        # Make init-check robust with retry loop
        for ic in pod_spec.get("initContainers", []):
            if ic.get("name") == "init-check":
                ic["command"] = ["sh", "-c", "apk add --no-cache netcat-openbsd >/dev/null 2>&1; until nc -z oran-smo-kafka 9092 && nc -z oran-smo-postgresql 5432; do echo 'Waiting for Kafka & PostgreSQL...'; sleep 3; done; echo 'All dependencies ready!'"]

        # Adjust health check probes for Spring Boot
        for c in pod_spec.get("containers", []):
            if "livenessProbe" in c:
                c["livenessProbe"]["initialDelaySeconds"] = 120
                c["livenessProbe"]["periodSeconds"] = 15
                c["livenessProbe"]["failureThreshold"] = 6
            if "readinessProbe" in c:
                c["readinessProbe"]["initialDelaySeconds"] = 120
                c["readinessProbe"]["periodSeconds"] = 15
                c["readinessProbe"]["failureThreshold"] = 6

        # Merge or set affinity
        aff = pod_spec.setdefault("affinity", {})
        aff["nodeAffinity"] = affinity_patch["nodeAffinity"]
        
    if kind in ["ServiceAccount", "Secret", "ConfigMap"]:
        rbac_secrets.append(doc)
    elif kind in ["Service", "NetworkPolicy", "PodDisruptionBudget"]:
        services_net.append(doc)
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
write_docs("20-services-and-policies.yaml", services_net)
write_docs("30-statefulsets.yaml", statefulsets)
write_docs("40-deployments.yaml", deployments)

print("==> All SMO GitOps manifests successfully generated!")
EOF
