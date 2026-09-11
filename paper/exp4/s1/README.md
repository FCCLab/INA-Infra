# Exp4 Scheme 1 (S1) — +PL only

Dedicated deploy package for **S1**: PL on, PM frozen at peak \(\bar T\), PS off
(equal PRB, `nws-xapp` replicas = 0). Namespace: **`exp4-s1`**.

Copied from S0 (same workloads, IPs, compute). Only sites change —
CU-UP / UPF / APP co-located (no N6 hairpin):

| Slice | DL app | CU-UP / UPF / APP | N6 IP | UE console |
| :---: | :--- | :--- | :--- | :--- |
| 1 | iperf3 + SFTP 5 MB | C / C / **C** | `10.1.137.211` | http://10.1.137.221/ |
| 2 | YOLO bbox (CCTV) | **E / E / E** | `10.1.137.212` | http://10.1.137.222/ |
| 3 | OTT / gstreamer watch | **R / R / R** | `10.1.137.213` | http://10.1.137.223/ |
| 4 | CPU offload | C / C / **C** | `10.1.137.214` | http://10.1.137.224/ |
| 5 | MQTT Get | C / C / **C** | `10.1.137.215` | http://10.1.137.225/ |

See [`../README.md`](../README.md) for the full S0–S3 matrix.

## Deploy

```bash
cd /home/fcp/INA-Infra
export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge

# Render + copy into repos/ + push Gitea (Config Sync)
python3 paper/exp4/s1/deploy.py

# Dry run (no Gitea push)
python3 paper/exp4/s1/deploy.py --no-push

# After RootSync is healthy:
./scripts/check-configsync.sh
python3 paper/exp4/s1/deploy_ue.py            # all five UEs
python3 paper/exp4/s1/deploy_ue.py --ue 5     # MQTT only
python3 paper/exp4/s1/deploy_ue.py 1,2,3,4
```

Confirm S1 co-location: slice 1/4/5 APP+UPF on **central**, slice 2 on **edge**, slice 3 on **regional**.

## Undeploy

```bash
python3 paper/exp4/s1/undeploy.py           # UEs + GitOps ns + cluster ns
python3 paper/exp4/s1/undeploy.py --ue-only
python3 paper/exp4/s1/undeploy.py --no-push
```

## Layout

```
paper/exp4/s1/
├── README.md
├── scheme.py              # S1 constants (sites, SLAs, IPs)
├── generate_gitops.py     # render → gitops_manifests/
├── deploy.py              # render + copy to repos/ + Gitea push
├── deploy_ue.py           # 5 UEs on edge usrp
├── undeploy.py
├── undeploy_ue.py
├── gitops_manifests/      # generated
└── manifests/             # generated UE YAML
```

Do not run this while `exp1-*` or another `exp4-s*` scheme is live on the same RAN.
