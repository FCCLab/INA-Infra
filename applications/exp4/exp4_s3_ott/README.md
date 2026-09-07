# Exp4 slice 3 — OTT / gstreamer **watch DL**

Copied from `applications/servers/ott` + `applications/clients/ott`.
Same N6 `10.1.137.213`, slice **3**. Playback is downlink only (no UL ingest).

| Path | Role |
| :--- | :--- |
| `server/` | Backend: MediaMTX + FastAPI catalog `:8080` |
| `server/frontend-console/` | Server console `:80` → backend |
| `client/backend/` | UE watch backend `:8090` |
| `client/frontend-console/` | UE console `:80` → backend |

SLA: \(\bar T = 22\) Mbit/s, \(\bar D = 58\) ms.

GitOps mounts `server/` as ConfigMap `application-ott-code`.
