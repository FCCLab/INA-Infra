# Exp4 Scheme 3 (S3) — +PL +PM +PS

Dedicated package for **S3**: same PL sites as S1, PM on, PS on
(`nws-xapp` replicas = 1, live PRB from η). Namespace: **`exp4-s3`**.

| Slice | DL app | CU-UP / UPF / APP | N6 IP |
| :---: | :--- | :--- | :--- |
| 1 | iperf3 + SFTP 5 MB | C / C / C | `10.1.137.211` |
| 2 | YOLO bbox (CCTV) | E / E / E | `10.1.137.212` |
| 3 | OTT / gstreamer watch | R / R / R | `10.1.137.213` |
| 4 | CPU offload | C / C / C | `10.1.137.214` |
| 5 | MQTT Get | C / C / C | `10.1.137.215` |

```bash
python3 paper/exp4/s3/deploy.py
./scripts/check-configsync.sh
python3 paper/exp4/s3/deploy_ue.py
python3 paper/exp4/s3/undeploy.py
```

One scheme live at a time on the shared RAN. See [`../README.md`](../README.md).
