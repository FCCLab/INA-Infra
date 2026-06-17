#!/usr/bin/env bash
# Export live central-cluster workloads to plain YAML for Gitea nephio/central-repo.
#
#   export KUBECONFIG=$HOME/.kube/config:$HOME/.kube/config-central
#   ./scripts/export-central-live.sh
#   ./scripts/push-central-to-gitea.sh
#
# Does NOT export config-management-system (Config Sync stays outside this git repo).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/initial_central}"
CTX="${KCTX:-central@central}"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config:$HOME/.kube/config-central}"

# Platform + workload namespaces on central (extend when OAI NFs are deployed).
NAMESPACES="${EXPORT_NAMESPACES:-local-path-storage default metallb-system kubernetes-dashboard}"

KINDS="${EXPORT_KINDS:-configmap secret service serviceaccount deployment statefulset daemonset ingress networkpolicy role rolebinding}"

CLUSTER_KINDS="${EXPORT_CLUSTER_KINDS:-storageclass}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Export running central cluster resources as cleaned YAML under initial_central/.

Options:
  -o DIR     Output directory (default: initial_central)
  -c CTX     kubectl context (default: central@central)
  -h         Help

Environment:
  KUBECONFIG          Merged kubeconfig (default: ~/.kube/config:~/.kube/config-central)
  EXPORT_NAMESPACES   Space-separated namespace list
  EXPORT_KINDS        Space-separated namespaced kinds
  SKIP_SECRETS=1      Omit Secret objects

After export: ./scripts/push-central-to-gitea.sh
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
  echo "error: kubectl context '$CTX' not reachable (set KUBECONFIG?)" >&2
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
# central cluster initial export

Generated by \`scripts/export-central-live.sh\` from kubectl context \`$CTX\`.
Output directory: \`initial_central/\`.

- \`namespaces/<ns>/\` — namespaced workloads on central
- \`cluster/\` — cluster-scoped platform resources

**Not exported:** \`config-management-system\`, \`resource-group-system\`, \`kube-system\`, Pods, Porch package revisions.

OAI packages from Nephio Approve also land in \`nephio/central-repo\` via Porch — this export is for live cluster GitOps baseline.

Re-export after manual changes, then \`./scripts/push-central-to-gitea.sh\`.

Check sync: \`./scripts/check-configsync.sh -c central@central -n central-repo\`
EOF

echo ""
echo "Done: $(find "$OUT_DIR" -name '*.yaml' | wc -l) YAML files in $OUT_DIR"
echo "Next: ./scripts/push-central-to-gitea.sh"
