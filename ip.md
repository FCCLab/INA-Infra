# MetalLB VIPs (10.1.132.10–79)

| VIP | Cluster | Service | Ports |
|-----|---------|---------|-------|
| 10.1.132.30 | mgmt | Docker Registry | 5000 |
| 10.1.132.40 | mgmt | Kubernetes Dashboard | 443 |
| 10.1.132.41 | central | Kubernetes Dashboard | 443 |
| 10.1.132.50 | mgmt | OpenSpeedTest | 80 |
| 10.1.132.51 | mgmt | Gitea | 22, 80, 3000 |
| 10.1.132.52 | mgmt | Nephio Web UI | 80 |
| 10.1.132.60 | central | OpenSpeedTest | 80 |
| 10.1.132.61 | central | Open5GS 5GC | 9999, 38412/sctp, 2152/udp |

Node IPs: mgmt `10.1.132.200`/`201`, central `10.1.132.210`/`211`.
