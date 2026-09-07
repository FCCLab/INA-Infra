# Exp4 slice 4 — file encrypt (generate → encrypt → download → delete)

Simulated 5G: `10.140.4.1`. Console: `http://10.1.137.214/`. \(\bar T = 8\) Mbit/s, \(\bar D = 400\) ms (relaxed, no headline SLA).

Continuous queue pipeline (autostarts):

```
generate file → queue → encrypt/zip → queue → download to UE → success → delete
```

Bounded queues (`EXP4_PLAIN_QUEUE_DEPTH` / `EXP4_READY_QUEUE_DEPTH`, default 2) prefetch so the UE always has a zip ready. After a successful download the zip is deleted on the server and the local UE copy is removed.

| Role | What |
| :--- | :--- |
| Server backend | `server/server.py` `:8080` — workers fill the queues; `GET /download` pops the next zip |
| Server console | `server/frontend-console/` on `:80` → backend (`/download` is streamed through) |
| Client backend | `client/backend/backend.py` `:8090` — autostart loop GET `/download`, log success, delete |
| Client console | `client/frontend-console/` on `:80` → backend |

GitOps mounts `server/server.py` as ConfigMap `application-cpu-offload-code`.
