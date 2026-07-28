#!/usr/bin/env bash
# Render OAI Kopf controllers into the profile namespace only (default: ina-infra).
#
# Single namespace + single GitOps dir:
#   repos/{central,regional,edge}-repo/namespaces/<profile>/70-*
#
# Base manifests: ina-infra/oai-controller-base/
# Utils (amd64):   ina-infra/oai-controller-utils/
#
# Removes leftover namespaces/oai-cn-operators/ and namespaces/ina-cn-operators/.
# CRB subjects are pinned to <profile> only (lab operator SA subjects dropped).
#
#   ./scripts/render_ina_cn_operators_gitops.sh [profile_ns]
#   ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'OAI controllers in ina-infra' central regional edge
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
PROFILE_NS="${1:-ina-infra}"
SMF_N4="${INA_SMF_N4:-10.1.140.12}"
NRF_LB="${OAI_NRF_LB_IP:-${OAI_NRF_LB:-10.1.138.100}}"
BASE_DIR="${INA_OAI_CONTROLLER_BASE:-$REPO_ROOT/ina-infra/oai-controller-base}"
UTILS_DIR="${INA_OAI_UTILS_DIR:-$REPO_ROOT/ina-infra/oai-controller-utils}"
FILE_PREFIX="70-"
DROP_OPS_NS=("oai-cn-operators" "ina-cn-operators")

python3 - "$REPOS_DIR" "$PROFILE_NS" "$SMF_N4" "$NRF_LB" "$BASE_DIR" "$UTILS_DIR" "$FILE_PREFIX" \
  "${DROP_OPS_NS[@]}" <<'PY'
from __future__ import annotations

import pathlib
import re
import shutil
import sys

import yaml

(
    repos_s,
    profile_ns,
    smf_n4,
    nrf_lb,
    base_dir_s,
    utils_dir_s,
    file_prefix,
    *drop_ops_ns,
) = sys.argv[1:]
repos = pathlib.Path(repos_s)
base_dir = pathlib.Path(base_dir_s)
utils_dir = pathlib.Path(utils_dir_s)
ops_ns = profile_ns

if not base_dir.is_dir():
    raise SystemExit(f"controller base missing: {base_dir}")

CORE_NFS = ("nrf", "ausf", "udm", "udr", "amf", "smf")
ALL_NFS = CORE_NFS + ("upf",)
REPO_FOR = {
    "central": "central-repo",
    "regional": "regional-repo",
    "edge": "edge-repo",
}


def dump(doc, path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def out_name(raw: str) -> str:
    if raw.startswith(file_prefix):
        return raw
    return f"{file_prefix}{raw}"


def patch_kopf_namespace(doc: dict, namespace: str) -> None:
    spec = doc["spec"]["template"]["spec"]
    for c in spec.get("containers", []):
        c["command"] = [
            "/usr/local/bin/python",
            "/root/.local/bin/kopf",
            "run",
            "/root/.local/controller.py",
            f"--namespace={namespace}",
        ]
        c.pop("args", None)


def patch_smf_nf_conf(doc: dict) -> None:
    tpl = doc["data"]["smf.yaml"]
    # register_nf MUST stay "no": with yes, this OAI SMF build never sends
    # N4 ASSOCIATION SETUP to the local upfs[] list (0 PFCP packets). AMF then
    # uses static nfs.smf (enable_smf_selection: no) instead of NRF for SMF.
    tpl = tpl.replace(
        "register_nf:\n  general: yes",
        "register_nf:\n  general: no",
        1,
    )
    old = """  upfs:
    {% set n3_addrs = ['10.1.139.20', '10.1.139.23', '10.1.139.26', '10.1.139.29', '10.1.139.32'] %}
    {% set slice_sds = ['000001', '000002', '000003', '000004', '000005'] %}
    {%- for i in conf['upfs'] %}
    - host: {{ i }}
      config:
        enable_usage_reporting: no
        n3_local_ipv4: {{ n3_addrs[loop.index0] if loop.index0 < (n3_addrs|length) else n3_addrs[0] }}
      upf_info:
        sNssaiUpfInfoList:
          - sNssai:
              sst: 1
              sd: "{{ slice_sds[loop.index0] if loop.index0 < (slice_sds|length) else slice_sds[0] }}"
            dnnUpfInfoList:
              - dnn: oai{{ loop.index }}
    {%- endfor %}"""
    new = """  upfs:
    {% set slice_sds = ['000001', '000002', '000003', '000004', '000005'] %}
    {%- for i in conf['upfs']|sort %}
    {%- set octets = i.split('.') %}
    {%- set n3_host = octets[0] ~ '.' ~ octets[1] ~ '.' ~ octets[2] ~ '.' ~ (octets[3]|int - 20) %}
    - host: {{ i }}
      config:
        enable_usage_reporting: no
        n3_local_ipv4: {{ n3_host }}
      upf_info:
        sNssaiUpfInfoList:
          - sNssai:
              sst: 1
              sd: "{{ slice_sds[loop.index0] if loop.index0 < (slice_sds|length) else slice_sds[0] }}"
            dnnUpfInfoList:
              - dnn: oai{{ loop.index }}
    {%- endfor %}"""
    if "octets[3]|int - 20" not in tpl:
        if old not in tpl:
            raise SystemExit("SMF nf-conf upfs block not found for patch")
        tpl = tpl.replace(old, new, 1)
    doc["data"]["smf.yaml"] = tpl


def patch_amf_nf_conf(doc: dict) -> None:
    """Use static nfs.smf (oai-smf) — required when SMF register_nf is no."""
    tpl = doc["data"]["amf.yaml"]
    if "enable_smf_selection: no" in tpl:
        doc["data"]["amf.yaml"] = tpl
        return
    if "enable_smf_selection: yes" not in tpl:
        raise SystemExit("AMF nf-conf enable_smf_selection not found for patch")
    doc["data"]["amf.yaml"] = tpl.replace(
        "enable_smf_selection: yes",
        "enable_smf_selection: no",
        1,
    )


def patch_upf_op_conf(doc: dict, smf: str, nrf: str) -> None:
    raw = doc["data"]["upf.yaml"]
    raw = re.sub(r"smf: '[^']*'", f"smf: '{smf}'", raw)
    raw = re.sub(r"nrf: '[^']*'", f"nrf: '{nrf}'", raw)
    doc["data"]["upf.yaml"] = raw


def patch_utils_amd64(utils_py: str) -> str:
    if '"kubernetes.io/arch": "amd64"' in utils_py:
        return utils_py
    node_sel = (
        '                      "spec": {\n'
        '                        "nodeSelector": {\n'
        '                          "kubernetes.io/arch": "amd64"\n'
        '                        },\n'
    )
    for old, tail in (
        (
            '                      "spec": {\n                        "affinity": {',
            '                        "affinity": {',
        ),
        (
            '                      "spec": {\n                        "securityContext": {',
            '                        "securityContext": {',
        ),
    ):
        if old in utils_py:
            return utils_py.replace(old, node_sel + tail, 1)
    raise SystemExit("controller-utils: cannot inject amd64 nodeSelector")


def clear_operator_files(dest: pathlib.Path) -> None:
    if not dest.is_dir():
        return
    for p in list(dest.iterdir()):
        if not p.is_file():
            continue
        name = p.name
        is_op = (
            "oai-" in name
            and (
                "controller" in name
                or "operator" in name
                or name.endswith("-op-conf.yaml")
                or name.endswith("-nf-conf.yaml")
                or name.endswith("-controller-utils.yaml")
            )
        )
        if name.startswith(file_prefix) and is_op:
            p.unlink()
            continue
        if name.startswith(
            ("serviceaccount-oai-", "configmap-oai-", "deployment-oai-")
        ) and is_op:
            p.unlink()


def write_utils_configmap(dest_ops: pathlib.Path, nf: str) -> None:
    src = utils_dir / f"{nf}.py"
    if not src.is_file():
        print(f"  WARN missing utils {src}")
        return
    text = patch_utils_amd64(src.read_text())
    dump(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"oai-{nf}-controller-utils",
                "namespace": ops_ns,
                "labels": {
                    "app.kubernetes.io/name": f"oai-{nf}",
                    "app.kubernetes.io/part-of": "ina-infra",
                    "app.kubernetes.io/component": "oai-controller",
                    "ina-infra.nephio.lab/arch": "amd64",
                },
            },
            "data": {"utils.py": text},
        },
        dest_ops / out_name(f"configmap-oai-{nf}-controller-utils.yaml"),
    )


def ensure_utils_mount(doc: dict, nf: str) -> None:
    spec = doc["spec"]["template"]["spec"]
    containers = spec.get("containers") or []
    if not containers:
        return
    c = containers[0]
    mounts = c.setdefault("volumeMounts", [])
    want_mount = {
        "name": "utils-patch",
        "mountPath": "/root/.local/utils.py",
        "subPath": "utils.py",
    }
    if not any(m.get("name") == "utils-patch" for m in mounts):
        mounts.append(want_mount)
    volumes = spec.setdefault("volumes", [])
    want_vol = {
        "name": "utils-patch",
        "configMap": {"name": f"oai-{nf}-controller-utils"},
    }
    if not any(v.get("name") == "utils-patch" for v in volumes):
        volumes.append(want_vol)


def copy_nf(dest_ops: pathlib.Path, nf: str, watch_ns: str) -> None:
    sa = base_dir / f"serviceaccount-oai-{nf}-operator.yaml"
    if sa.is_file():
        doc = yaml.safe_load(sa.read_text())
        doc["metadata"]["namespace"] = ops_ns
        doc["metadata"].setdefault("labels", {})
        doc["metadata"]["labels"]["app.kubernetes.io/part-of"] = "ina-infra"
        doc["metadata"]["labels"]["app.kubernetes.io/component"] = "oai-controller"
        dump(doc, dest_ops / out_name(sa.name))

    for kind in ("op-conf", "nf-conf"):
        name = f"configmap-oai-{nf}-{kind}.yaml"
        src = base_dir / name
        if not src.is_file():
            continue
        doc = yaml.safe_load(src.read_text())
        doc["metadata"]["namespace"] = ops_ns
        doc["metadata"].setdefault("labels", {})
        doc["metadata"]["labels"]["app.kubernetes.io/part-of"] = "ina-infra"
        doc["metadata"]["labels"]["app.kubernetes.io/component"] = "oai-controller"
        if nf == "smf" and kind == "nf-conf":
            patch_smf_nf_conf(doc)
        if nf == "amf" and kind == "nf-conf":
            patch_amf_nf_conf(doc)
        if nf == "upf" and kind == "op-conf":
            patch_upf_op_conf(doc, smf=smf_n4, nrf=nrf_lb)
        dump(doc, dest_ops / out_name(name))

    write_utils_configmap(dest_ops, nf)

    dep = base_dir / f"deployment-oai-{nf}-controller.yaml"
    if dep.is_file():
        doc = yaml.safe_load(dep.read_text())
        doc["metadata"]["namespace"] = ops_ns
        doc["metadata"].setdefault("labels", {})
        doc["metadata"]["labels"]["app.kubernetes.io/part-of"] = "ina-infra"
        doc["metadata"]["labels"]["app.kubernetes.io/component"] = "oai-controller"
        patch_kopf_namespace(doc, watch_ns)
        spec = doc["spec"]["template"]["spec"]
        spec.setdefault("nodeSelector", {})["kubernetes.io/arch"] = "amd64"
        ensure_utils_mount(doc, nf)
        dump(doc, dest_ops / out_name(dep.name))


def pin_crb_subject(cluster_dir: pathlib.Path, nf: str, sa_ns: str) -> None:
    """Keep a single SA subject: <sa_ns>/oai-<nf>-operator."""
    path = cluster_dir / f"clusterrolebinding-oai-{nf}-operator-rolebinding-cluster.yaml"
    if not path.is_file():
        src = repos / "central-repo" / "cluster" / path.name
        if src.is_file() and cluster_dir != src.parent:
            cluster_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, path)
            cr = (
                repos
                / "central-repo"
                / "cluster"
                / f"clusterrole-oai-{nf}-operator-cluster-role.yaml"
            )
            if cr.is_file():
                shutil.copy2(cr, cluster_dir / cr.name)
        else:
            print(f"  WARN missing CRB {path}")
            return
    doc = yaml.safe_load(path.read_text())
    want = {
        "kind": "ServiceAccount",
        "name": f"oai-{nf}-operator",
        "namespace": sa_ns,
    }
    doc["subjects"] = [want]
    dump(doc, path)
    print(f"  CRB subject → {sa_ns}/oai-{nf}-operator")


def drop_ops_dirs(repo_name: str) -> None:
    for ns in drop_ops_ns:
        path = repos / repo_name / "namespaces" / ns
        if path.exists():
            shutil.rmtree(path)
            print(f"  removed {path.relative_to(repos)}")


# --- central: full core + upf ---
dest_central = repos / "central-repo" / "namespaces" / profile_ns
dest_central.mkdir(parents=True, exist_ok=True)
clear_operator_files(dest_central)
drop_ops_dirs("central-repo")
for nf in ALL_NFS:
    copy_nf(dest_central, nf, profile_ns)
    pin_crb_subject(repos / "central-repo" / "cluster", nf, ops_ns)
print(f"Wrote controllers → {dest_central} (watch ns={profile_ns})")

# --- regional / edge: UPF only ---
for site in ("regional", "edge"):
    repo_name = REPO_FOR[site]
    dest = repos / repo_name / "namespaces" / profile_ns
    drop_ops_dirs(repo_name)
    dest.mkdir(parents=True, exist_ok=True)
    clear_operator_files(dest)
    copy_nf(dest, "upf", profile_ns)
    pin_crb_subject(repos / repo_name / "cluster", "upf", ops_ns)
    print(f"Wrote UPF controller → {dest} (watch ns={profile_ns})")

print("Done.")
print(
    f"Next: ./bringup/03_push_to_git_repos/push_git_repos.sh "
    f"-m 'OAI controllers only in {profile_ns}; drop oai-cn-operators' "
    f"central regional edge"
)
PY
