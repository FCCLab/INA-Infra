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
#   # profile_ns defaults to ina-infra (NOT a cluster name — do not pass "central")
#   ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'ina NRF LB + UPF peers' central regional edge
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=cluster_lib.sh
source "$SCRIPT_DIR/cluster_lib.sh"

REPOS_DIR="${REPOS_DIR:-$REPO_ROOT/repos}"
PROFILE_NS="${1:-ina-infra}"
SMF_N4="${INA_SMF_N4:-10.1.140.12}"
# Cross-cluster UPF→NRF Nnrf on Multus (same /24 as UPF N3/N4/N6).
NRF_SBI="${INA_NRF_SBI_IP:-${INA_NRF_LB_IP:-${INA_NRF_LB:-$(ina_nrf_sbi_ip)}}}"
BASE_DIR="${INA_OAI_CONTROLLER_BASE:-$REPO_ROOT/ina-infra/oai-controller-base}"
UTILS_DIR="${INA_OAI_UTILS_DIR:-$REPO_ROOT/ina-infra/oai-controller-utils}"
FILE_PREFIX="70-"
DROP_OPS_NS=("oai-cn-operators" "ina-cn-operators")

python3 - "$REPOS_DIR" "$PROFILE_NS" "$SMF_N4" "$NRF_SBI" "$BASE_DIR" "$UTILS_DIR" "$FILE_PREFIX" \
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
    nrf_sbi,
    base_dir_s,
    utils_dir_s,
    file_prefix,
    *drop_ops_ns,
) = sys.argv[1:]
repos = pathlib.Path(repos_s)
base_dir = pathlib.Path(base_dir_s)
utils_dir = pathlib.Path(utils_dir_s)
ops_ns = profile_ns

sys.path.insert(0, str(repos.parent / "ina-infra" / "backend"))
try:
    from app.services import multus_iface
except ImportError:
    multus_iface = None  # type: ignore


def cluster_multus_parent(cluster: str) -> str:
    if multus_iface is None:
        return "enp7s0"
    return multus_iface.detect_cluster_master(cluster)

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


def upsert_env(container: dict, name: str, value: str) -> None:
    env = container.setdefault("env", [])
    for e in env:
        if e.get("name") == name:
            e["value"] = value
            return
    env.append({"name": name, "value": value})


def patch_nrf_controller_lb(doc: dict, lb_ip: str) -> None:
    """Legacy no-op: UPFs reach NRF via Multus Nnrf, not MetalLB .138."""
    del doc, lb_ip
    return


def quote_snssai_sd(tpl: str) -> str:
    """Match oai-slice-deployment / oai-cn: snssais.sd must be YAML-quoted strings.

    Unquoted ``sd: 000001`` is YAML 1.1 octal → breaks SMF UPF selection by S-NSSAI+DNN
    (fallback to any UPF; only the lucky slice works).
    """
    return tpl.replace(
        "sd: {{ s['sd'] if 'sd' in s.keys() else 'FFFFFF' }}",
        "sd: \"{{ s['sd'] if 'sd' in s.keys() else 'FFFFFF' }}\"",
    )


def patch_smf_nf_conf(doc: dict) -> None:
    """Match oai-slice / oai-cn: register_nf yes, discover_upf no, static upfs[].

    IPs are 10.1.140.* (ina). Inline sNssai required for UPF selection by S-NSSAI+DNN.
    """
    tpl = doc["data"]["smf.yaml"]
    tpl = tpl.replace(
        "register_nf:\n  general: no",
        "register_nf:\n  general: yes",
        1,
    )
    tpl = quote_snssai_sd(tpl)
    tpl = tpl.replace("discover_upf: yes", "discover_upf: no")
    new = """  upfs:
    {% set n3_addrs = ['10.1.140.21', '10.1.140.22', '10.1.140.23', '10.1.140.24', '10.1.140.25'] %}
    {% set slice_sds = ['000001', '000002', '000003', '000004', '000005'] %}
    {%- for i in conf['upfs']|sort %}
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
    tpl, nsub = re.subn(
        r"  upfs:\n    \{%.*?\{%- endfor %\}",
        new,
        tpl,
        count=1,
        flags=re.S,
    )
    if not nsub:
        raise SystemExit("SMF nf-conf upfs block not found/patched (oai-slice-style)")
    doc["data"]["smf.yaml"] = tpl


def patch_amf_nf_conf(doc: dict) -> None:
    """NRF SMF selection (oai-cn / oai-slice): enable_smf_selection yes with SMF register_nf."""
    tpl = quote_snssai_sd(doc["data"]["amf.yaml"])
    tpl = tpl.replace(
        "enable_smf_selection: no",
        "enable_smf_selection: yes",
        1,
    )
    doc["data"]["amf.yaml"] = tpl


def patch_upf_nf_conf(doc: dict) -> None:
    """Quote SDs + register with NRF (matches working oai-upf / oai-slice stack)."""
    tpl = quote_snssai_sd(doc["data"]["upf.yaml"])
    tpl = tpl.replace("register_nf:\n  upf: 'no'", "register_nf:\n  upf: 'yes'", 1)
    doc["data"]["upf.yaml"] = tpl


def patch_upf_op_conf(doc: dict, smf: str, nrf: str, parent: str) -> None:
    raw = doc["data"]["upf.yaml"]
    raw = re.sub(r"smf: '[^']*'", f"smf: '{smf}'", raw)
    raw = re.sub(r"nrf: '[^']*'", f"nrf: '{nrf}'", raw)
    # Auto-fill Multus parent NIC (enp7s0 on VMs, eno1 on bare-metal, …).
    raw = re.sub(r"parent: '[^']*'", f"parent: '{parent}'", raw, count=1)
    doc["data"]["upf.yaml"] = raw


def patch_op_conf_nrf_ip(doc: dict, key: str, nrf: str) -> None:
    """Point fqdn.nrf at Multus Nnrf IP (not DNS name oai-nrf)."""
    if key not in doc.get("data", {}):
        return
    raw = doc["data"][key]
    raw2, n = re.subn(
        r"nrf:\s*'[^']*'",
        f"nrf: '{nrf}'",
        raw,
        count=1,
    )
    if not n:
        raw2, n = re.subn(
            r"nrf:\s*oai-nrf\b",
            f"nrf: '{nrf}'",
            raw,
            count=1,
        )
    if not n:
        raise SystemExit(f"op-conf {key}: fqdn.nrf not found to set Nnrf IP")
    doc["data"][key] = raw2


def patch_nf_op_conf_parent(doc: dict, key: str, parent: str) -> None:
    """Set nad.parent in amf/smf/upf op-conf jinja blobs."""
    if key not in doc.get("data", {}):
        return
    raw = doc["data"][key]
    if "parent:" not in raw:
        return
    doc["data"][key] = re.sub(
        r"parent: '[^']*'", f"parent: '{parent}'", raw, count=1
    )


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
    # Init wait default: Multus Nnrf IP (same as fqdn.nrf in op-conf).
    text = text.replace(
        'nrf_svc = "oai-nrf" #default value',
        f'nrf_svc = "{nrf_sbi}" #default value (Multus Nnrf)',
    )
    text = text.replace(
        "until {URL}; do echo waiting for oai-nrf; sleep 1; done",
        f"until {{URL}}; do echo waiting for nrf svc {{nrf_svc}}; sleep 1; done",
    )
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


def copy_nf(dest_ops: pathlib.Path, nf: str, watch_ns: str, *, cluster: str) -> None:
    parent = cluster_multus_parent(cluster)
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
        if nf == "upf" and kind == "nf-conf":
            patch_upf_nf_conf(doc)
        if nf == "upf" and kind == "op-conf":
            patch_upf_op_conf(doc, smf=smf_n4, nrf=nrf_sbi, parent=parent)
        if nf in ("amf", "smf", "ausf", "udm", "udr") and kind == "op-conf":
            patch_op_conf_nrf_ip(doc, f"{nf}.yaml", nrf_sbi)
        if nf in ("amf", "smf", "nrf") and kind == "op-conf":
            key = f"{nf}.yaml"
            patch_nf_op_conf_parent(doc, key, parent)
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
        if nf == "nrf":
            # ClusterIP for in-cluster AUSF/UDM/UDR; UPFs use Multus Nnrf (nrf_sbi).
            for c in doc["spec"]["template"]["spec"].get("containers") or []:
                upsert_env(c, "SVC_TYPE", "ClusterIP")
                c["env"] = [
                    e for e in c.get("env") or [] if e.get("name") != "LOADBALANCER_IP"
                ]
        spec = doc["spec"]["template"]["spec"]
        spec.setdefault("nodeSelector", {})["kubernetes.io/arch"] = "amd64"
        ensure_utils_mount(doc, nf)
        dump(doc, dest_ops / out_name(dep.name))
    print(f"  {nf}@{cluster}: multus parent={parent}")


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
    copy_nf(dest_central, nf, profile_ns, cluster="central")
    pin_crb_subject(repos / "central-repo" / "cluster", nf, ops_ns)
print(f"Wrote controllers → {dest_central} (watch ns={profile_ns}, UPF nrf={nrf_sbi})")

# Drop accidental operator dump into namespaces/central (cluster name ≠ profile ns).
if profile_ns != "central":
    accidental = repos / "central-repo" / "namespaces" / "central"
    if accidental.is_dir() and any(accidental.glob("70-*oai-*")):
        for p in accidental.glob("70-*oai-*"):
            p.unlink()
            print(f"  removed accidental {p.relative_to(repos)}")

# --- regional / edge: UPF only ---
for site in ("regional", "edge"):
    repo_name = REPO_FOR[site]
    dest = repos / repo_name / "namespaces" / profile_ns
    drop_ops_dirs(repo_name)
    dest.mkdir(parents=True, exist_ok=True)
    clear_operator_files(dest)
    copy_nf(dest, "upf", profile_ns, cluster=site)
    pin_crb_subject(repos / repo_name / "cluster", "upf", ops_ns)
    print(f"Wrote UPF controller → {dest} (watch ns={profile_ns})")
    if profile_ns != "central":
        accidental = repos / repo_name / "namespaces" / "central"
        if accidental.is_dir() and any(accidental.glob("70-*oai-*")):
            for p in accidental.glob("70-*oai-*"):
                p.unlink()
                print(f"  removed accidental {p.relative_to(repos)}")

print("Done.")
print(
    f"Next: ./bringup/03_push_to_git_repos/push_git_repos.sh "
    f"-m 'ina NRF Nnrf {nrf_sbi}; UPF peers use Multus' "
    f"central regional edge"
)
PY
