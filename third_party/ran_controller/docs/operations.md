# Operations

## INA-Infra usage

In this testbed, Config Sync often **prunes operator-created** Deployments/pods. RAN GitOps scripts therefore also render **static executor** manifests (Deployment + ConfigMap + NADs) alongside the operator Deployment. Prefer those executors for long-lived RAN stacks; keep the image from [build.md](build.md) in sync if you still run the operator.

| Path | Role |
|------|------|
| Operator Deployment | `oai-ran-operators` (or per-slice ns) — watches `NFDeployment` |
| Executor manifests | Written by `scripts/render_oai_ran_*.sh` / `render_oai_slice_deployment_gitops.sh` |
| Lab image default in scripts | `docker.io/nephio/oai-ran-controller:latest` — override with `OAI_RAN_OPERATOR_IMAGE` |

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
