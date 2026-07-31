# OpenSpeedTest utilities

Throughput and ping helpers for lab OpenSpeedTest (OST) servers, plus a client container image.

## OST servers (MetalLB)

| Site | URL |
|------|-----|
| mgmt (default) | http://10.1.132.11/ |
| central | http://10.1.138.101/ |
| regional | http://10.1.138.126/ |
| edge | http://10.1.138.151/ |

## UE path (via PDU tunnel)

Runs on the **edge** control plane (`UE_HOST=edge-0` by default — needs `/etc/kubernetes/admin.conf`). Discovers `oai-*` / `ina-*` UEs with an `oaitun_*` address, then binds traffic to the UE IP.

```bash
cd utils/openspeedtest

./list_ues.sh
./speedtest.sh 1 -d 10 --threads 1
./speedtest.sh 1 --dir download -d 0 --threads 1   # forever DL (Ctrl+C)
./speedtest.sh 1 --dir upload -d 0 --threads 1     # forever UL
./pingtest.sh 1 --count 5
```

`-d 0` / `--duration forever` / `inf` runs until Ctrl+C; pick `--dir` / `--direction` as `download` or `upload` (not `both`). Use `--threads 1` on RFsim. Env: `UE_HOST`, `OST_SERVER`, `TUN_MTU`, `SSH_CONFIG`, `KUBECONFIG_REMOTE`.

## Client container (host / cluster path)

Image wraps `speedtest.py` (no UE tunnel). Registry: `10.1.132.30:5000/openspeedtest-client:latest`.

```bash
# Build + push
./build_push.sh

# Run (host network so MetalLB VIPs are reachable)
./run_client.sh -d 10 --threads 1
./run_client.sh --dir download -d 0 --threads 1
./run_client.sh --dir upload -d 0 --threads 1

docker run --rm --network host 10.1.132.30:5000/openspeedtest-client:latest \
  --dir upload -d 0 -t 1
```

## Layout

| File | Role |
|------|------|
| `speedtest.py` | CLI client (download/upload to OST `/downloading` + `/upload`) |
| `speedtest.sh` / `pingtest.sh` / `list_ues.sh` | UE discovery + kubectl exec via `ue_common.sh` |
| `Dockerfile` / `build_push.sh` / `run_client.sh` | Client image build, push, run |
