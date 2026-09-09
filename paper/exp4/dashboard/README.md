# Exp4 All Applications Grafana

One board for all five DL slices. Influx `application_metrics`, `profile_name=exp4`.

| Slice | `app_type` | Server | Client |
| :---: | :--- | :--- | :--- |
| 1 | `exp4-s1` | CPU / RAM / GPU / VRAM; DL Send-Q / notsent on `TO_CLIENT_IFACE` | DL throughput, latencies, Recv-Q / rwnd on `TO_SERVER_IFACE` |
| 2 | `exp4-s2` | same | same |
| 3 | `exp4-s3` | same | same |
| 4 | `exp4-s4` | same | same |
| 5 | `exp4-s5` | same | same |

**Scheme** dropdown filters `scheme` (`exp4-s0` … `exp4-s3`, `exp4-no5g`). One scheme is live at a time; All overlays history.

Top group: same 4+4 layout as the per-app boards, all five slices overlaid on each graph. Next rows: one pane per application for server DL TCP **Send-Q** / **notsent** on `TO_CLIENT_IFACE` (`net1`), then client DL TCP **Recv-Q** / **rwnd** on `TO_SERVER_IFACE` (`oaitun*`). Expand a slice row for that app only. Application latency is app work (`t_send` → client). Transmission latency is ICMP RTT on `TO_SERVER_IFACE` (TCP connect fallback). E2E is application + transmission.

Regenerate:

```bash
python3 paper/exp4/dashboard/generate.py
python3 paper/exp4/dashboard/generate.py --push
```

Grafana: `http://10.1.137.105:3000` (`inainfra` / `inainfra`). UID `ina-exp4-apps`.
