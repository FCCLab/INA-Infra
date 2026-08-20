---
name: apps-gitops
description: >-
  Procedures for building, configuring, deploying, and troubleshooting end-to-end
  5G slice applications (OTT Streaming, CCTV Analytics, Physical-AI, IoT, AIPerf)
  across edge, regional, and central clusters with Multus N6 and 5G PDU routing.
---

# 5G Slice Applications GitOps Skill

Covers the architecture, code synchronization, container image building, and runtime diagnostics for all 5G network slice applications in `INA-Infra`.

---

## 1. Slice Applications Matrix

| Slice | App Type | Server Component | Client (UE) Component | Data Plane / Protocol |
| :--- | :--- | :--- | :--- | :--- |
| **Slice 1** | `cctv` | `application-cctv` (Regional/Edge) | `oai-ue-slice-1-client-1/2` (Edge) | RTSP `/slicea_cam1`, YOLOv8 detection |
| **Slice 2** | `physical-ai` | `application-physical-ai` (Edge) | `oai-ue-slice-2-client-1` (Edge) | Real-time telemetry, WebRTC/WebSocket |
| **Slice 3** | `ott` | `application-ott` (Central) | `oai-ue-slice-3-client-1` (Edge) | 4K YouTube playback via 5G PDU tunnel |
| **Slice 4** | `iot` | `application-iot` (Central) | `oai-ue-slice-4-client-1` (Edge) | MQTT / CoAP / HTTP sensor metrics |

---

## 2. Server Source Code Mounting & ConfigMap Rules

> **Important**: Application server pods mount Python scripts from a ConfigMap (e.g. `application-ott-code`, `application-cctv-code`) under `/app/server/` or `/app/edge/`.
> 
> Modifying source files in `applications/<app>/` requires updating **both** the local code and the GitOps ConfigMap manifest:

### Syncing Python Changes to Running Pods:
```bash
# 1. Update source file locally (e.g. applications/ott/server/state.py)
# 2. Sync to mgmt-0 backend workspace:
scp -F utils/ssh_config/config applications/ott/server/* mgmt-0:/home/fcp/INA-Infra/applications/ott/server/

# 3. Update GitOps ConfigMap manifest (e.g. repos/central-repo/namespaces/ina-infra/60-app-3-application-ott-code-configmap.yaml):
kubectl create configmap application-ott-code --from-file=/home/fcp/INA-Infra/applications/ott/server/ -n ina-infra --dry-run=client -o yaml > /tmp/cm.yaml
# (Keep labels: app.kubernetes.io/name, ina.lab/slice, ina.lab/app-type)

# 4. Push to GitOps:
./bringup/03_push_to_git_repos/push_gitea_gitops.sh -m "fix(ott): update server logic" central

# 5. Restart application pod:
kubectl --context=central@central delete pod -n ina-infra -l app.kubernetes.io/name=application-ott
```

---

## 3. PDU Routing & Multus N6 Networking

- **Multus N6 IPs**:
  - Slice 1 Server: `10.1.137.211`
  - Slice 2 Server: `10.1.137.212`
  - Slice 3 Server: `10.1.137.213`
  - Slice 4 Server: `10.1.137.214`
- **PDU Tunnel Interface**: `oaitun_ue<slice_id>` (IP range `10.140.<slice_id>.<client_idx+1>`).
- **PDU SOCKS Proxy**: Ran inside UE sidecar on port `1080` to route traffic over 5G PDU tunnel to the application server.

---

## 4. Diagnostics & Health Probes

```bash
# Check OTT Server Health & Status:
kubectl --context=central@central exec -n ina-infra -l app.kubernetes.io/name=application-ott -c application-backend -- curl -s http://127.0.0.1:8080/api/v1/status

# Test UE PDU connection to Server:
kubectl --context=edge@edge exec -n ina-infra -l app.kubernetes.io/name=oai-ue-slice-3-client-1 -c debug -- curl -s http://10.1.137.213/api/v1/health

# Check UE Backend Playback / Watchdog Logs:
kubectl --context=edge@edge logs -n ina-infra -l app.kubernetes.io/name=oai-ue-slice-3-client-1 -c backend --tail=30
```
