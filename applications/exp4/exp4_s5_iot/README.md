# Exp4 slice 5 — MQTT Get (DL telemetry)

Copied from `applications/servers/iot` + `applications/clients/iot`.
Remapped from Exp1 slice 4 / `.214` → Exp4 slice **5** / `10.1.137.215`.
Topics default to `slice_5/...` (not `slice_d`).

DL fan-out default: **50 ms** × **12500 B** ≈ **2 Mbit/s** (SLA \(\bar D = 80\) ms, \(\bar T = 2\) Mbit/s).
Client UL default: **50 Hz** × **5000 B** ≈ **2 Mbit/s**. Console is stats-only (Mbps / Hz).

| Path | Role |
| :--- | :--- |
| `server/` | Backend: Mosquitto + DL controller + `control_api.py` `:8080` |
| `server/frontend-console/` | Server console `:80` → backend |
| `client/backend/` | UE MQTT backend `:8090` |
| `client/frontend-console/` | UE console `:80` → backend |
