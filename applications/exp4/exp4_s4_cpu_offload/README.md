# Exp4 slice 4 — same as slice 1, plus encrypt

Simulated 5G: `10.140.4.1`. Console: `http://10.1.137.214/`.

Slice 4 is the slice-1 SFTP + iperf3 path with **server-side encrypt** of each 1 MB file before it is queued. E2E latency is generate-start → last SFTP byte, so it is higher than slice 1 by the encrypt work.

| Role | What |
| :--- | :--- |
| Server backend | `iperf3 -s :5201` + sshd SFTP + `control_api.py` `:8080` (generate → encrypt → queue) |
| Server console | `server/frontend-console/` on `:80` → backend |
| Client backend | `client/backend/backend.py` `:8090` (SFTP / iperf of encrypted 1 MB files) |
| Client console | `client/frontend-console/` on `:80` → backend |

```
t_send (filename) → urandom 1 MB → PBKDF2+XOR encrypt → SFTP to UE → last byte = t_recv
e2e_ms = (t_recv - t_send) * 1000
```

```bash
# on the UE (sim 5G net1)
python3 applications/exp4/exp4_s4_cpu_offload/client/sftp_dl.py --host 10.140.4.1
```
