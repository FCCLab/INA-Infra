# Exp4 slice 5 — MQTT Get (DL telemetry)

Copied from `applications/servers/iot` + `applications/clients/iot`.
Remapped from Exp1 slice 4 / `.214` → Exp4 slice **5** / `10.1.137.215`.
Topics default to `slice_5/...` (not `slice_d`).

DL fan-out default: **3000 msg/s** × **128 B** ≈ **3.07 Mbit/s** per device (`DL_MSGS_PER_S` / `DL_PAYLOAD_BYTES`; msgs/s `0` = max rate).
Client UL default: **50 Hz** × **5000 B** ≈ **2 Mbit/s**. UE consoles **subscribe** to DL; the **server** console **starts/stops** generate+publish and sets msgs/s (estimated Mbps shown).

| Path | Role |
| :--- | :--- |
| `server/` | Backend: Mosquitto + DL controller + `control_api.py` `:8080` |
| `server/frontend-console/` | Server console `:80` → start/stop generate, msgs/s, est. Mbps |
| `client/backend/` | UE MQTT backend `:8090` (subscribes `slice_5/dl/<dev>`) |
| `client/frontend-console/` | UE console `:80` → backend |
