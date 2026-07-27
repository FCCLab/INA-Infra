"""Multus IP allocation: host = base[role] + n for slice-scoped roles."""

from __future__ import annotations

import ipaddress
from typing import Dict, List, Mapping, Optional, Sequence

from app.schemas import (
    IpPlan,
    PlacementOut,
    Profile,
    SharedIps,
    SliceIn,
    SliceIps,
)

# Per-role base host octets on profile subnet (plan defaults).
SHARED_BASES: Dict[str, int] = {
    "gw_central": 1,
    "gw_regional": 2,
    "gw_edge": 3,
    "amf_n2": 10,
    "smf_n4": 12,
    "cucp_n2": 200,
    "cucp_f1c": 201,
    "cucp_e1": 202,
    "du_f1": 203,
    "du_rf": 204,
    "flexric_e2": 205,
    "xapp_e2": 206,
}

# Slice-scoped: host = base + n (n = 1..N)
SLICE_BASES: Dict[str, int] = {
    "upf_n3": 20,
    "upf_n4": 40,
    "upf_n6": 60,
    "cuup_e1": 80,
    "cuup_f1u": 100,
    "cuup_n3": 120,
    "ue_rf": 140,
}

SITE_TO_CLUSTER = {0: "edge", 1: "regional", 2: "central"}


def default_profile() -> Profile:
    return Profile()


def _prefix_and_len(subnet: str) -> tuple[str, int]:
    net = ipaddress.ip_network(subnet, strict=False)
    if net.version != 4 or net.prefixlen != 24:
        raise ValueError(f"profile subnet must be IPv4 /24, got {subnet!r}")
    # "10.1.140"
    parts = str(net.network_address).split(".")
    return ".".join(parts[:3]), int(net.prefixlen)


def _host(prefix: str, octet: int) -> str:
    if not 1 <= octet <= 254:
        raise ValueError(f"host octet out of range: {octet}")
    return f"{prefix}.{octet}"


def _validate_bases(max_slices: int) -> None:
    for role, base in SLICE_BASES.items():
        last = base + max_slices
        if last > 254:
            raise ValueError(
                f"role {role} base={base} + max_slices={max_slices} overflows /24"
            )
    # Disjoint check: each slice range [base+1, base+max] must not overlap others
    ranges: List[tuple[str, int, int]] = []
    for role, base in SLICE_BASES.items():
        ranges.append((role, base + 1, base + max_slices))
    for i, (r1, lo1, hi1) in enumerate(ranges):
        for r2, lo2, hi2 in ranges[i + 1 :]:
            if lo1 <= hi2 and lo2 <= hi1:
                raise ValueError(f"IP ranges overlap: {r1}[{lo1}-{hi1}] vs {r2}[{lo2}-{hi2}]")
    shared_octets = set(SHARED_BASES.values())
    for role, base in SLICE_BASES.items():
        for n in range(1, max_slices + 1):
            h = base + n
            if h in shared_octets:
                raise ValueError(f"slice role {role} host {h} collides with shared base")


def allocate_profile_ips(
    profile: Profile,
    slices: Sequence[SliceIn],
    deploy_map: Optional[Mapping[str, PlacementOut]] = None,
) -> IpPlan:
    """Build IpPlan for current slice list. Raises ValueError on overflow."""
    n = len(slices)
    if n < 1:
        raise ValueError("need at least one slice for IP allocation")
    if n > profile.max_slices:
        raise ValueError(
            f"N={n} exceeds profile.max_slices={profile.max_slices}"
        )
    _validate_bases(profile.max_slices)

    prefix, plen = _prefix_and_len(profile.subnet)
    shared = SharedIps(
        gw_central=_host(prefix, SHARED_BASES["gw_central"]),
        gw_regional=_host(prefix, SHARED_BASES["gw_regional"]),
        gw_edge=_host(prefix, SHARED_BASES["gw_edge"]),
        amf_n2=_host(prefix, SHARED_BASES["amf_n2"]),
        smf_n4=_host(prefix, SHARED_BASES["smf_n4"]),
        cucp_n2=_host(prefix, SHARED_BASES["cucp_n2"]),
        cucp_f1c=_host(prefix, SHARED_BASES["cucp_f1c"]),
        cucp_e1=_host(prefix, SHARED_BASES["cucp_e1"]),
        du_f1=_host(prefix, SHARED_BASES["du_f1"]),
        du_rf=_host(prefix, SHARED_BASES["du_rf"]),
        flexric_e2=_host(prefix, SHARED_BASES["flexric_e2"]),
        xapp_e2=_host(prefix, SHARED_BASES["xapp_e2"]),
        gateway=_host(prefix, SHARED_BASES["gw_central"]),
        prefix_len=plen,
    )

    out_slices: List[SliceIps] = []
    for idx, s in enumerate(slices, start=1):
        place = (deploy_map or {}).get(str(s.id))
        cu_id = place.cu_id if place else -1
        upf_id = place.upf_id if place else -1
        app_id = place.app_id if place else -1
        out_slices.append(
            SliceIps(
                n=idx,
                slice_id=s.id,
                upf_n3=_host(prefix, SLICE_BASES["upf_n3"] + idx),
                upf_n4=_host(prefix, SLICE_BASES["upf_n4"] + idx),
                upf_n6=_host(prefix, SLICE_BASES["upf_n6"] + idx),
                cuup_e1=_host(prefix, SLICE_BASES["cuup_e1"] + idx),
                cuup_f1u=_host(prefix, SLICE_BASES["cuup_f1u"] + idx),
                cuup_n3=_host(prefix, SLICE_BASES["cuup_n3"] + idx),
                ue_rf=_host(prefix, SLICE_BASES["ue_rf"] + idx),
                dnn_cidr=f"{profile.dnn_prefix}.{idx}.0/24",
                site_cu=place.cu if place else "",
                site_upf=place.upf if place else "",
                site_app=place.app if place else "",
                cluster_cu=SITE_TO_CLUSTER.get(cu_id, ""),
                cluster_upf=SITE_TO_CLUSTER.get(upf_id, ""),
            )
        )

    bases = {**SHARED_BASES, **SLICE_BASES}
    return IpPlan(
        profile=profile,
        subnet=profile.subnet,
        n_slices=n,
        shared=shared,
        slices=out_slices,
        bases=bases,
    )
