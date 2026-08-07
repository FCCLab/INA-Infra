# INA-Infra hybrid deploy (mgmt)

Gurobi **Named-User** academic licenses cannot run in pods. Workaround:

| Piece | Where |
|-------|--------|
| API + Gurobi | Host process on mgmt-0 (`./run-backend.sh` or systemd) |
| UI | Kubernetes Deployment, NodePort **30518** |
| UI → API | Service `ina-infra-backend` + Endpoints → `10.1.132.200:8082` |
| RAN operator agent → API | **WebSocket** from edge `oai-ran-operator` to host `:8082` (`/api/v1/operators/ws`) |

## Setup

```bash
cd /home/fcp/nephio-network-slicing

# 1) Host API (keep this running)
./ina-infra/run-backend.sh
# optional persistent: sudo ./ina-infra/scripts/install-host-backend.sh

# 2) Images + GitOps UI
./ina-infra/scripts/build-and-push-images.sh
./scripts/render_ina_infra_gitops.sh mgmt

# Remove old in-cluster backend Deployment if present
kubectl --context mgmt@mgmt -n ina-infra delete deploy ina-infra-backend --ignore-not-found
kubectl --context mgmt@mgmt -n ina-infra delete secret gurobi-license --ignore-not-found

kubectl --context mgmt@mgmt apply -f repos/mgmt/namespaces/ina-infra/
# or: ./bringup/03_push_to_git_repos/push_git_repos.sh -m 'ina-infra hybrid' mgmt
```

## Access

| | |
|--|--|
| UI | http://10.1.132.200:30518 |
| API | http://10.1.132.200:8082/docs |

## Full K8s later

Replace Named-User with an [Academic WLS](https://support.gurobi.com/hc/en-us/articles/13210193318033-What-is-an-Academic-WLS-license) license, then both API and UI can be Deployments.
