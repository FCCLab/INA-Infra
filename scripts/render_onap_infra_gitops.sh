#!/usr/bin/env bash
# ==============================================================================
# Render ONAP Infrastructure (ChartMuseum, ACM Runtime, K8s Participant)
# GitOps manifests for edge cluster under repos/edge-repo/namespaces/neuro-ran-smo/onap/
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_DIR="${ROOT_DIR}/repos/edge-repo/namespaces/neuro-ran-smo/onap"

export PATH="${HOME}/.local/bin:${PATH}"

mkdir -p "${OUTPUT_DIR}"

TMP_BUILD_DIR=$(mktemp -d /tmp/onap_infra_build.XXXXXX)
trap 'rm -rf "${TMP_BUILD_DIR}"' EXIT

TEMP_RAW="${TMP_BUILD_DIR}/onap_raw.yaml"
echo "==> Rendering ONAP Infrastructure Helm templates..."

# 1. ChartMuseum
helm template chartmuseum oran-release/chartmuseum \
  --namespace onap \
  --set global.persistence.storageClass=local-path \
  --set global.githubContainerRegistry=docker.io \
  --set image=chartmuseum/chartmuseum:latest \
  --set global.pullPolicy=IfNotPresent \
  > "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 2. ACM Runtime
helm template acm-runtime oran-release/policy-clamp-runtime-acm \
  --namespace onap \
  --set global.persistence.storageClass=local-path \
  --set readinessCheck.enabled=false \
  --set global.kafkaBootstrap=strimzi-kafka-bootstrap:9092 \
  >> "${TEMP_RAW}"

echo "---" >> "${TEMP_RAW}"

# 3. K8s Participant
helm template k8s-ppnt oran-release/policy-clamp-ac-k8s-ppnt \
  --namespace onap \
  --set readinessCheck.enabled=false \
  --set global.kafkaBootstrap=strimzi-kafka-bootstrap:9092 \
  --set 'repoList.helm.repos[0].repoName=local' \
  --set 'repoList.helm.repos[0].address=http://chartmuseum.onap.svc.cluster.local:8080' \
  >> "${TEMP_RAW}"

echo "==> Processing and injecting node affinity into ONAP manifests..."
TEMP_RAW="${TEMP_RAW}" OUTPUT_DIR="${OUTPUT_DIR}" python3 - <<'EOF'
import yaml
import sys
import os

temp_raw = os.environ["TEMP_RAW"]
out_dir = os.environ["OUTPUT_DIR"]

with open(temp_raw, "r") as f:
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
        "name": "onap",
        "labels": {
            "app.kubernetes.io/part-of": "neuro-ran-smo",
            "app.kubernetes.io/managed-by": "configsync"
        }
    }
}

rbac_secrets = [
    {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "policy-clamp-ac-k8s-ppnt-ku", "namespace": "onap"},
        "type": "Opaque",
        "stringData": {"sasl.jaas.config": ""}
    },
    {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "policy-clamp-runtime-acm-ku", "namespace": "onap"},
        "type": "Opaque",
        "stringData": {"sasl.jaas.config": ""}
    }
]
services_net = [
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "policy-pg-primary", "namespace": "onap"},
        "spec": {
            "type": "ExternalName",
            "externalName": "oran-smo-postgresql.smo.svc.cluster.local"
        }
    },
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "strimzi-kafka-bootstrap", "namespace": "onap"},
        "spec": {
            "type": "ExternalName",
            "externalName": "oran-smo-kafka.smo.svc.cluster.local"
        }
    },
    {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "k8s-strimzi-kafka-bootstrap", "namespace": "onap"},
        "spec": {
            "type": "ExternalName",
            "externalName": "oran-smo-kafka.smo.svc.cluster.local"
        }
    }
]
pvcs = []
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
            meta["namespace"] = "onap"

    # Patch Kafka server and PostgreSQL server in ConfigMaps
    if kind == "ConfigMap":
        data = doc.get("data", {})
        for k, v in data.items():
            if isinstance(v, str):
                data[k] = v.replace("k8s-strimzi-kafka-bootstrap:9092", "oran-smo-kafka.smo.svc.cluster.local:9092") \
                           .replace("acm-strimzi-kafka-bootstrap:9092", "oran-smo-kafka.smo.svc.cluster.local:9092") \
                           .replace("policy-pg-primary", "oran-smo-postgresql.smo.svc.cluster.local") \
                           .replace("k8s-", "oran-smo-kafka.smo.svc.cluster.local:9092") \
                           .replace("security.protocol: SASL_PLAINTEXT", "security.protocol: PLAINTEXT") \
                           .replace("sasl.mechanism: SCRAM-SHA-512", "sasl.mechanism: PLAIN") \
                           .replace("sasl.jaas.config: ${SASL_JAAS_CONFIG}", "sasl.jaas.config: org.apache.kafka.common.security.plain.PlainLoginModule required username=\"\" password=\"\";")
    
    if kind in ["Deployment", "StatefulSet", "DaemonSet"]:
        spec = doc.setdefault("spec", {})
        template = spec.setdefault("template", {})
        pod_spec = template.setdefault("spec", {})
        
        # Remove readiness init container if present
        init_containers = pod_spec.get("initContainers", [])
        pod_spec["initContainers"] = [c for c in init_containers if not c.get("name", "").endswith("-readiness")]
        
        # Remove duplicate logback.xml volume mount so startup script cp succeeds
        for c in pod_spec.get("containers", []):
            v_mounts = c.get("volumeMounts", [])
            c["volumeMounts"] = [m for m in v_mounts if m.get("mountPath") != "/opt/app/policy/clamp/etc/logback.xml"]
        
        aff = pod_spec.setdefault("affinity", {})
        aff["nodeAffinity"] = affinity_patch["nodeAffinity"]
        
    if kind in ["ServiceAccount", "Secret", "ConfigMap", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"]:
        rbac_secrets.append(doc)
    elif kind in ["Service", "NetworkPolicy", "PodDisruptionBudget"]:
        services_net.append(doc)
    elif kind == "PersistentVolumeClaim":
        pvcs.append(doc)
    elif kind in ["Deployment", "StatefulSet"]:
        deployments.append(doc)
    else:
        rbac_secrets.append(doc)

def write_docs(filename, doc_list):
    path = os.path.join(out_dir, filename)
    with open(path, "w") as f:
        yaml.dump_all(doc_list, f, sort_keys=False)
    print(f"Wrote {len(doc_list)} resources to {path}")

# Write organized manifests
write_docs("00-namespace.yaml", [namespace_doc])
write_docs("10-config-and-secrets.yaml", rbac_secrets)
write_docs("20-services-and-pvcs.yaml", services_net + pvcs)
write_docs("30-deployments.yaml", deployments)

print("==> All ONAP Infrastructure GitOps manifests successfully generated!")
EOF
