# Open5GS 5GC (Kubernetes)

Monolithic Open5GS 5GC from [FCCLab/5gc-open5gs](https://github.com/FCCLab/5gc-open5gs), deployed on the **edge** cluster via Config Sync.

Default placement: **`cpu-edge-1`** (`enp7s0`). `edge-2` is not currently a Kubernetes node.

N2/N3/Web UI: Multus macvlan **`10.1.137.107`** on site L2. RAN should use PLMN **001/01** and TAC **81**.

## Flow

```bash
# 1. Build + push image to 10.1.132.30:5000
./services/open5gs/build_push.sh

# 2. Render GitOps (optional node: edge-1 | edge-2)
./scripts/render_open5gs_gitops.sh
# ./scripts/render_open5gs_gitops.sh edge-2

# 3. Push to Gitea → Config Sync
./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Open5GS 5GC on edge' edge
```

## Verify

```bash
kubectl --context edge@edge -n open5gs get pods -o wide
kubectl --context edge@edge -n open5gs logs -l app.kubernetes.io/name=open5gs-5gc --tail=50
# Web UI (Next.js binds the pod Flannel IP; port-forward from the operator host)
kubectl --context edge@edge -n open5gs port-forward svc/open5gs-5gc 9999:9999
# then http://127.0.0.1:9999
```

| Endpoint | Address |
|----------|---------|
| Web UI | `kubectl --context edge@edge -n open5gs port-forward svc/open5gs-5gc 9999:9999` → http://127.0.0.1:9999 |
| AMF NGAP (N2) | `10.1.137.107:38412` SCTP |
| UPF GTP-U (N3) | `10.1.137.107:2152` UDP |
| DNS | `open5gs.edge.inainfra` (N2/N3 VIP) |

UE pool: `10.45.0.0/24` (ogstun). Subscribers: `subscriber_db.csv` (IMSI `001010000000001`…).
