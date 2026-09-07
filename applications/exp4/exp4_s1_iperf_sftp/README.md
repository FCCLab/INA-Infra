# Exp4 slice 1 — iperf3 + SFTP 5 MB (DL)

Dedicated copy for Experiment 4. Simulated 5G: `10.140.1.1`. Console: `http://10.1.137.211/`.

| Role | What |
| :--- | :--- |
| Server backend | `iperf3 -s :5201` + sshd SFTP + `control_api.py` `:8080` |
| Server console | `server/frontend-console/` on `:80` → backend |
| Client backend | `client/backend/backend.py` `:8090` (SFTP / iperf) |
| Client console | `client/frontend-console/` on `:80` → backend |

SLA (info only): \(\bar T = 20\) Mbit/s, \(\bar D = 250\) ms. Headline metric is **SFTP goodput** \(40\,\text{Mbit}/T_\text{transfer}\), not MAC rate.

```bash
# on the UE (sim 5G net1)
python3 applications/exp4/exp4_s1_iperf_sftp/client/sftp_dl.py --host 10.140.1.1
applications/exp4/exp4_s1_iperf_sftp/client/iperf_dl.sh
```
