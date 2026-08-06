# Operations

## INA-Infra usage

### oai-benchmark (operator-driven RAN)

[`scripts/render_oai_benchmark_gitops.sh`](../../../scripts/render_oai_benchmark_gitops.sh) deploys **`oai-ran-controller`** in `oai-benchmark` on **edge**. It reconciles `NFDeployment`s `cucp-bench` / `cuup-bench` / `du-bench` into ConfigMaps, Deployments, and Services. Multus NADs stay in git (names must match `{nfdeployment}-{iface}`). UPF and nrUE remain static executors.

```bash
# Image must include rf Multus + usrp pin (see ina-infra-oai-ran-controller DuResources)
./third_party/ran_controller/scripts/build_ran_controller_image.sh
export OAI_RAN_OPERATOR_IMAGE=10.1.132.30:5000/oai-ran-controller:latest
./scripts/render_oai_benchmark_gitops.sh
./bringup/03_push_to_git_repos/push_git_repos.sh central edge
kubectl --context edge@edge -n oai-benchmark get nfdeployment,deploy
```

### Other RAN GitOps (hybrid)

In this testbed, Config Sync often **prunes** or conflicts with operator-created workloads when the same objects are also owned by git. Legacy RAN scripts therefore also render **static executor** manifests alongside the operator. Prefer executors for long-lived non-benchmark stacks; keep the image from [build.md](build.md) in sync if you still run the operator.

| Path | Role |
|------|------|
| Operator Deployment | `oai-ran-operators` or workload ns — watches `NFDeployment` |
| Executor manifests | Written by `scripts/render_oai_ran_*.sh` / `render_oai_slice_deployment_gitops.sh` |
| Lab image default in scripts | Override with `OAI_RAN_OPERATOR_IMAGE` (registry `10.1.132.30:5000/oai-ran-controller`) |

Verify sync: `./scripts/check-configsync.sh`. Topology / IPs: [`docs/oai.md`](../../../docs/oai.md).

## Telnet O1 (DU)

The DU Deployment enables OAI telnet (`--telnetsrv …`); Service **`oai-du-telnet-lb`** exposes TCP **9090** (LoadBalancer; NodePort scaffold `32500`).

```bash
export KUBECONFIG=~/.kube/config:~/.kube/config-edge
# Namespace depends on package (e.g. oai-ran-du or slice ns)
TELNET_IP=$(kubectl --context edge@edge get svc oai-du-telnet-lb -n oai-ran-du \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo o1 stats | nc -N "$TELNET_IP" 9090
```

### Bandwidth reconfigure (20 → 40 MHz)

Requires matching UE image/config after the change (see submodule `test-infra/oai-ue/`).

```bash
echo o1 stop_modem | nc -N "$TELNET_IP" 9090
echo o1 bwconfig 40 | nc -N "$TELNET_IP" 9090
echo o1 start_modem | nc -N "$TELNET_IP" 9090
echo o1 stats | nc -N "$TELNET_IP" 9090
```

Full Nephio kind/Gitea walk-through for local blueprints: [`test-infra/README.md`](../../ina-infra-oai-ran-controller/test-infra/README.md).

## Status and logs

```bash
kubectl get nfdeployment -A
kubectl describe nfdeployment <name> -n <ns>   # conditions: invalidProvider, …
kubectl logs -n oai-ran-operators deploy/oai-ran-operator -f
```

## Known limitations

1. **No in-place `NFDeployment` updates** — recreate the CR (or rely on GitOps executor rewrite + pod restart).
2. **Create-once semantics** — finalizer path creates resources once; failed mid-create may need manual cleanup of partial SA/CM/Deploy/Svc.
3. **Config Sync pruning** — do not assume operator-owned pods survive RootSync without matching objects in git (executor pattern).
4. Upstream README still references specialized Nephio packages; lab IPs and namespaces follow INA-Infra render scripts, not catalog defaults alone.
