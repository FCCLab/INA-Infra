# Exp4 Scheme 1 (S1) — +PL only

Dedicated deploy package for **S1**: PL on, PM frozen at peak \(\bar T\), PS off
(equal PRB, `nws-xapp` replicas = 0). Namespace: **`exp4-s1`**.

Five downlink slices, co-located CU-UP / UPF / APP:

| Slice | DL app | Site | N6 IP |
| :---: | :--- | :--- | :--- |
| 1 | iperf3 + SFTP 5 MB | central | `10.1.137.211` |
| 2 | YOLO bbox (CCTV analyzer) | edge | `10.1.137.212` |
| 3 | OTT / gstreamer watch | regional | `10.1.137.213` |
| 4 | CPU offload (encrypt/zip/LUT) | central | `10.1.137.214` |
| 5 | MQTT Get (IoT broker) | central | `10.1.137.215` |

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
python3 paper/exp4/s1/deploy_ue.py
```

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
