#!/usr/bin/env bash
# Export live mgmt-cluster workloads to plain YAML for Gitea nephio/mgmt (Config Sync unstructured).
#
#   export KUBECONFIG=$HOME/.kube/config
#   ./scripts/export-mgmt-live.sh
#   ./scripts/push-mgmt-to-gitea.sh
#
# Does NOT export config-management-system (Config Sync must stay outside this git repo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/initial_mgmt}"
CTX="${KCTX:-mgmt@mgmt}"

# App namespaces currently running on mgmt (edit if you add/remove components).
NAMESPACES="${EXPORT_NAMESPACES:-gitea porch-system porch-fn-system nephio-system nephio-webui backend-system network-config kubernetes-dashboard metallb-system local-path-storage default}"

# Namespaced kinds to capture (skip Pods/RS/Endpoints — noise).
KINDS="${EXPORT_KINDS:-configmap secret service serviceaccount deployment statefulset daemonset ingress networkpolicy role rolebinding}"

# Cluster-scoped kinds (optional platform bits).
CLUSTER_KINDS="${EXPORT_CLUSTER_KINDS:-storageclass}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Export running mgmt cluster resources as cleaned YAML under initial_mgmt/.

Options:
  -o DIR     Output directory (default: initial_mgmt)
  -c CTX     kubectl context (default: mgmt@mgmt)
  -h         Help

Environment:
  EXPORT_NAMESPACES   Space-separated namespace list
  EXPORT_KINDS        Space-separated namespaced kinds
  SKIP_SECRETS=1      Omit Secret objects (default: include — needed for Gitea etc.)

After export: ./scripts/push-mgmt-to-gitea.sh
EOF
}

while getopts "o:c:h" opt; do
  case "$opt" in
    o) OUT_DIR="$OPTARG" ;;
    c) CTX="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
  esac
done

if ! kubectl --context="$CTX" cluster-info >/dev/null 2>&1; then
  echo "error: kubectl context '$CTX' not reachable" >&2
  exit 1
fi

clean_yaml() {
  python3 - "$@" <<'PY'
import sys, yaml

skip_secret = sys.argv[-1] == "skip-secret"
if skip_secret:
    args = sys.argv[1:-1]
else:
    args = sys.argv[1:]

strip_meta = {
    "uid", "resourceVersion", "generation", "creationTimestamp",
    "managedFields", "selfLink", "ownerReferences",
}
strip_ann_prefixes = (
    "kubectl.kubernetes.io/",
    "deployment.kubernetes.io/",
    "meta.helm.sh/",
    "internal.kpt.dev/",
    "config.k8s.io/",
)

for path in args:
    with open(path, "r", encoding="utf-8") as fh:
        docs = list(yaml.safe_load_all(fh))
    out = []
    for doc in docs:
        if not doc or not doc.get("kind"):
            continue
        if skip_secret and doc.get("kind") == "Secret":
            continue
        doc.pop("status", None)
        meta = doc.setdefault("metadata", {})
        for k in strip_meta:
            meta.pop(k, None)
        ann = meta.get("annotations") or {}
        for k in list(ann):
            if any(k.startswith(p) for p in strip_ann_prefixes):
                ann.pop(k, None)
            # Runtime MetalLB bookkeeping — not needed in git.
            if k == "metallb.universe.tf/ip-allocated-from-pool":
                ann.pop(k, None)
        if not ann:
            meta.pop("annotations", None)
        spec = doc.get("spec") or {}
        if doc.get("kind") == "Service":
            for k in ("clusterIP", "clusterIPs", "healthCheckNodePort", "externalTrafficPolicy",
                      "internalTrafficPolicy", "ipFamilies", "ipFamilyPolicy"):
                spec.pop(k, None)
            doc["spec"] = spec
        out.append(doc)
    if not out:
        continue
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump_all(out, fh, default_flow_style=False, sort_keys=False)
PY
}

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/namespaces" "$OUT_DIR/cluster"

echo "Exporting context=$CTX -> $OUT_DIR"

for ns in $NAMESPACES; do
  if ! kubectl --context="$CTX" get namespace "$ns" >/dev/null 2>&1; then
    echo "skip missing namespace: $ns"
    continue
  fi
  ns_dir="$OUT_DIR/namespaces/$ns"
  mkdir -p "$ns_dir"
  for kind in $KINDS; do
    [[ "${SKIP_SECRETS:-0}" == "1" && "$kind" == "secret" ]] && continue
    items=$(kubectl --context="$CTX" get "$kind" -n "$ns" -o name 2>/dev/null || true)
    [[ -z "$items" ]] && continue
    while IFS= read -r item; do
      [[ -z "$item" ]] && continue
      name="${item#*/}"
      # default: only OpenSpeedTest (+ skip cluster noise)
      if [[ "$ns" == "default" ]]; then
        case "$kind/$name" in
          deployment/openspeedtest|service/openspeedtest-service) ;;
          *) continue ;;
        esac
      fi
      # Skip default/kubernetes service noise in default ns.
      if [[ "$ns" == "default" && "$kind" == "service" && "$name" == "kubernetes" ]]; then
        continue
      fi
      out="$ns_dir/${kind}-${name}.yaml"
      kubectl --context="$CTX" get "$kind" "$name" -n "$ns" -o yaml >"$out"
      if [[ "${SKIP_SECRETS:-0}" == "1" ]]; then
        clean_yaml "$out" skip-secret
      else
        clean_yaml "$out"
      fi
      echo "  $ns/$kind/$name"
    done <<< "$items"
  done
done

for kind in $CLUSTER_KINDS; do
  items=$(kubectl --context="$CTX" get "$kind" -o name 2>/dev/null || true)
  [[ -z "$items" ]] && continue
  while IFS= read -r item; do
    [[ -z "$item" ]] && continue
    name="${item#*/}"
    out="$OUT_DIR/cluster/${kind}-${name}.yaml"
    kubectl --context="$CTX" get "$kind" "$name" -o yaml >"$out"
    clean_yaml "$out"
    echo "  cluster/$kind/$name"
  done <<< "$items"
done

cat >"$OUT_DIR/README.md" <<EOF
# mgmt cluster initial export

Generated by \`scripts/export-mgmt-live.sh\` from kubectl context \`$CTX\`.
Output directory: \`initial_mgmt/\`.

- \`namespaces/<ns>/\` — namespaced workloads (Gitea, Porch, Nephio, WebUI, …)
- \`cluster/\` — cluster-scoped platform resources

**Not exported:** \`config-management-system\` (Config Sync), \`kube-system\`, Pods, dynamic Porch revisions.

Re-export after manual changes, then \`./scripts/push-mgmt-to-gitea.sh\`.
EOF

echo ""
echo "Done: $(find "$OUT_DIR" -name '*.yaml' | wc -l) YAML files in $OUT_DIR"
echo "Next: ./scripts/push-mgmt-to-gitea.sh"
