INA-Infra on mgmt

API runs on the host (Gurobi forbids containers):
  sudo ./ina-infra/scripts/install-host-backend.sh

UI: NodePort 30518 → http://10.1.132.200:30518
API: http://10.1.132.200:8082/docs
