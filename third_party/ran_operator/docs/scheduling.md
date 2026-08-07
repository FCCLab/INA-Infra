# CPU scheduling (CFS vs SCHED_RR)

OAI `nr-softmodem` threads can run under Linux **SCHED_OTHER** (CFS) or **SCHED_RR** (real-time round-robin).

## Current operator: no effect on SCHED_RR

The **current oai-ran-operator / ina-infra agent does not take effect on `SCHED_RR`.**

- Operators UI / HTTP / WebSocket **CPU update** only patches Kubernetes **CPU request/limit**.
- It does **not** set, clear, or report Linux scheduling policy (`chrt` / `SCHED_RR` / `SCHED_OTHER`).
- Changing CPU via the operator therefore **does not enable, disable, or alter RR**.

Scheduling policy is determined only by the pod **`securityContext`** when the container starts (create-once template or a manual/`patch_oai_benchmark_ran_vpc.sh` pin) — outside the operator resource-update path.

| Path | Affects CPU limit/request? | Affects SCHED_RR? |
|------|----------------------------|-------------------|
| Operators update API / agent | Yes | **No** (current operator) |
| Pod `securityContext` (privileged / caps) | No | Yes (at pod start only) |

## NFs where the CPU update API works (lab)

Operator id: **`edge-oai-benchmark`** (`oai-benchmark` on **edge**). Agent declares these NFs with `controllable: [cpu, memory]`; **only CPU apply is implemented** (memory is advertised, not applied).

| NF | Kind | CPU update API | CFS limit enforces? | Notes |
|----|------|----------------|---------------------|-------|
| **oai-cu-cp** | `cucp` | **Yes** (spec/`cpu.max` updated) | **Often no** if privileged → **SCHED_RR** | Same RR caveat as DU |
| **oai-cu-up** | `cuup` | **Yes** | **Yes** (SCHED_OTHER, non-privileged) | In-place patch; BestEffort→Burstable if needed |
| **oai-du** | `du` | **Yes** (spec/`cpu.max` updated) | **Often no** if privileged → **SCHED_RR** | API still patches resources; RR threads ignore CFS — operator does not change RR |

Not covered by this agent: UPF, UE, FlexRIC, or NFs outside the operator’s namespace discovery.

Trust **pod `spec` + node `cpu.max` / `chrt`**, not only kubelet `status.containerStatuses[].resources` (can lag after in-place resize).

## Why it matters for CPU limits

| Policy | How softmodem gets it | CFS `cpu.max` |
|--------|----------------------|---------------|
| **SCHED_OTHER** | non-privileged, no `SYS_NICE` | **Enforced** |
| **SCHED_RR** | privileged (or caps allowing `sched_setscheduler`) | **Bypassed** for RT threads |

Operator CPU apply is meaningful under **SCHED_OTHER**. If threads are already RR, limits can look applied in pod spec/`cpu.max` while RR threads ignore them — and the operator still cannot change that.

## How the pod gets SCHED_RR (not via operator)

- Non-privileged + drop `ALL`, add `NET_ADMIN` / `NET_RAW` / `IPC_LOCK` → typically **SCHED_OTHER**.
- `privileged: true` → softmodem may switch workers to **SCHED_RR**.

Code / pin (create-time only, not agent):

- CU: `resources_cucp.go` / `resources_cuup.go`
- DU: `resources_du.go`
- Lab pin: [`scripts/patch_oai_benchmark_ran_vpc.sh`](../../../scripts/patch_oai_benchmark_ran_vpc.sh)

## Verify (node `chrt`, not Operators API)

```bash
export KUBECONFIG=~/.kube/config-edge
NS=oai-benchmark
NF=oai-du   # or oai-cu-up / oai-cu-cp
POD=$(kubectl --context edge@edge -n "$NS" get pod -l app.kubernetes.io/name="$NF" -o jsonpath='{.items[0].metadata.name}')
NODE=$(kubectl --context edge@edge -n "$NS" get pod "$POD" -o jsonpath='{.spec.nodeName}')
CID=$(kubectl --context edge@edge -n "$NS" get pod "$POD" -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')

ssh -o BatchMode=yes "$NODE" "CID=$CID bash -s" <<'EOF'
PID=$(sudo crictl inspect "$CID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["pid"])')
SM=$(pgrep -P "$PID" | head -1)
echo "container=$PID softmodem=$SM"
for t in $(ls /proc/$SM/task 2>/dev/null | head -40); do sudo chrt -p "$t" 2>/dev/null; done \
  | awk '/policy/{print $NF}' | sort | uniq -c
EOF
```

`chrt` counts do not change when you call the Operators CPU update API.

See [operator-agent.md](operator-agent.md) for the CPU-only control surface.
