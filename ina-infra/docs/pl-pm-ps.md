# INA-Infra — Planning (PL), Medium (PM), Short (PS)

Three Gurobi MILP layers at different timescales. Implementation:
`algorithm/new_implementation/ina/` (`layer1_pl.py`, `layer2_pm.py`, `layer3_ps.py`).

## Architecture

| Layer | UI tab | When it runs | Fixed vs variable |
|---|---|---|---|
| **PL** | Planning | Manual **Solve PL** | Placement + initial compute + `b_min` |
| **PM** | Medium | Run once / loop | Compute only; placement fixed |
| **PS** | Short | Run once / loop | PRBs only; placement & compute unchanged |

```
PL  →  deploy_map, resources, b_min baseline
PS  →  b_min, b_ded, b_max, demand (radio potential)
PM  →  a_c_*, a_r_*, a_g_*  (reads demand from PS)
```

PM and PS loops are **independent** in the UI. Both require a successful PL result on the profile.

---

## Network substrate

Sites `j ∈ {0,1,2}` → **Edge / Regional / Central**.

Each slice places exactly one site for **CU-UP** (`x`), **UPF** (`y`), and **APP** (`z`).

### Default capacities (demo profile)

| Site | NF CPU `c_n` | NF RAM `r_n` | APP CPU | APP RAM | APP GPU |
|---:|---:|---:|---:|---:|---:|
| Edge (0) | 55 | 64 | 41 | 2600 | 22 |
| Regional (1) | 52 | 64 | 25 | 1400 | 12 |
| Central (2) | 61 | 64 | 90 | 5625 | 45 |

### Unit costs `p_c`, `p_r`, `p_g` (per site)

Edge is most expensive; Central is cheapest — solver prefers central/regional when delay SLAs allow.

| Site | CPU `p_c` | RAM `p_r` | GPU `p_g` |
|---:|---:|---:|---:|
| Edge | 2.5 | 0.5 | 2.5 |
| Regional | 0.25 | 0.05 | 0.5 |
| Central | 0.001 | 0.002 | 0.1 |

### PRB costs

| Code | Default | Meaning |
|---|---:|---|
| `p_prb_ded` | 0.5 | Dedicated PRB cost weight |
| `p_prb_prio` | 0.1 | Shared / priority PRB cost (`b_min − b_ded`) |

### Throughput coupling (bottleneck)

Planned / achievable throughput `T` is limited by **all** resource conversions:

```
T ≤ alpha_cu  × a_c_cu      (CU CPU → Mbps)
T ≤ alpha_upf × a_c_upf     (UPF CPU → Mbps)
T ≤ gamma_c   × a_c_app     (APP CPU → Mbps)
T ≤ gamma_r   × a_r_app     (APP RAM → Mbps)
T ≤ gamma_g   × a_g_app     (APP GPU → Mbps)
```

Defaults: `alpha_cu=1.02`, `alpha_upf=0.81`, `gamma_c=0.5`, `gamma_r=0.008`, `gamma_g=1.0`.

Effective throughput ≈ **min** of the five bounds (`compute_cap` in API output).

### Radio pool

| Code | Default | Meaning |
|---|---:|---|
| `b_total` | 273 | Total PRBs in the cell |
| PS `extra` | derived | `(b_total − Σ b_min) / n_slices` shared equally |
| `b_max` | derived | `b_min + extra` per slice |

Radio throughput ≈ `eta × b_max` (PS) or `eta_t0 × b_min` (PL planning estimate).

### Global objective weights

| Code | Default | Used by | Meaning |
|---|---:|---|---|
| `w_c` | 1.0 | PL, PM, PS | Weight on resource / PRB **cost** |
| `w_p` | 1000.0 | PL, PM, PS | Weight on SLA **shortfall** penalties |
| `beta_demand` | 0.1 | PM only | Extra weight on demand vs `t_bar` shortfall |

Keep `w_p` large so meeting SLAs dominates marginal cost savings.

### Delay model (PL only)

RTT-style hops (ms):

```
d_plan = d_rf + d_f1[CU site] + d_n3[CU site, UPF site] + d_n6[UPF site, APP site]
```

Default `d_rf = 20` (UE→DU). N6 cross-site penalties encourage UPF↔APP co-location.

---

## Slice SLAs (default 4-slice profile)

See `ina-infra/sla.md`.

| id | Type | `t_bar` | `d_bar` | `h_s` | `eta_t0` | Typical placement |
|---:|---|---:|---:|---:|---:|---|
| 1 | CCTV | 10 | 150 | 0 | 2.0 | CU@Edge; UPF+APP@Regional |
| 2 | Physical AI | 20 | 20 | 1 | 2.0 | All@Edge |
| 3 | OTT | 40 | 50 | 0 | 2.5 | CU@Regional; UPF+APP@Central |
| 4 | IoT | 5 | 150 | 0 | 1.5 | All@Central |

| Field | Code | PL | PM | PS |
|---|---|:---:|:---:|:---:|
| Throughput SLA | `t_bar` (T̄) | ✓ | ✓ | ✓ |
| Delay SLA | `d_bar` (D̄) | ✓ | — | — |
| Hard isolation | `h_s` | ✓ | — | ✓ |
| Planning η | `eta_t0` | ✓ | — | — |
| Runtime η | `eta` | — | — | ✓ |
| Demand target | `demand` | — | ✓ | writes |

When `h_s = 1`: `b_ded = b_min` (fully dedicated PRBs).

---

## Layer 1 — PL (PlanningLayer)

### Decisions

- Binary placement: `x[s,j]`, `y[s,j]`, `z[s,j]` (exactly one site each)
- Continuous compute: `a_c_cu`, `a_r_cu`, `a_c_upf`, `a_r_upf`, `a_c_app`, `a_r_app`, `a_g_app`
- Integer PRBs: `b_min`, `b_ded`
- Throughput / delay plans: `t_plan`, `d_plan`
- Shortfalls: `xi_d`, `xi_prb`, `xi_com`

### Cost (minimized, weighted by `w_c`)

**Infrastructure** (paid only at chosen site):

```
Σ_s,j  (a_c_cu·p_c[j] + a_r_cu·p_r[j]) · x[s,j]
     + (a_c_upf·p_c[j] + a_r_upf·p_r[j]) · y[s,j]
     + (a_c_app·p_c[j] + a_r_app·p_r[j] + a_g_app·p_g[j]) · z[s,j]
```

**PRBs:**

```
Σ_s  p_prb_ded · b_ded[s] + p_prb_prio · (b_min[s] − b_ded[s])
```

### Penalties (weighted by `w_p`)

```
Σ_s  (xi_d[s] + xi_prb[s] + xi_com[s])
```

Where (soft constraints):

- `xi_d[s] ≥ d_plan[s] − d_bar[s]` — delay shortfall
- `xi_prb[s] ≥ t_bar[s] − eta_t0[s] · b_min[s]` — radio shortfall at plan time
- `xi_com[s] ≥ t_bar[s] − t_plan[s]` — compute shortfall

### Key constraints

- One site per NF; site capacity sums
- `t_plan` coupling to all five resource types; minimum sizing for `t_bar`
- `b_ded ≤ b_min`; `b_ded ≥ b_min · h_s`
- `Σ b_min ≤ b_total`
- Delay linearization via `xy`, `yz` products for N3/N6 hops

### Outputs

`deploy_map`, `resources` (including `b_min`), Multus IP plan → profile + GitOps apply.

---

## Layer 2 — PM (MediumLayer)

### Decisions

Compute amounts only (no placement binaries, no PRBs):

`a_c_cu`, `a_r_cu`, `a_c_upf`, `a_r_upf`, `a_c_app`, `a_r_app`, `a_g_app`, `t_curr`, `xi_sla`, `xi_dem`

### Cost (`w_c`)

Same infrastructure cost as PL but at **fixed** sites from `deploy_map`.

### Penalties (`w_p`)

```
Σ_s  xi_sla[s] + beta_demand · xi_dem[s]
```

- `xi_sla[s] ≥ t_bar[s] − t_curr[s]`
- `xi_dem[s] ≥ demand[s] − t_curr[s]`

`demand[s]` comes from PS loop state (or `t_bar` if PS has not run).

### Key constraints

- Site capacity at fixed locations
- Same five throughput coupling inequalities as PL
- Does **not** re-check delay

---

## Layer 3 — PS (ShortLayer)

### Decisions

Integer `b_min[s]`, `b_ded[s]`; shortfall `xi_prb[s]`.

### Cost (`w_c`)

```
Σ_s  p_prb_ded · b_ded[s] + p_prb_prio · (b_min[s] − b_ded[s])
```

### Penalties (`w_p`)

```
Σ_s  xi_prb[s]   where   xi_prb[s] ≥ t_bar[s] − eta[s] · b_min[s]
```

### Key constraints

- `Σ b_min ≤ b_total`
- `b_ded ≤ b_min`; `b_ded ≥ b_min · h_s`
- Post-solve: `extra = (b_total − Σ b_min) / n`, `b_max = b_min + extra`

`eta[s]` from current MCS via `EtaCalculator` (or `mcs_fixed` in loop params).

---

## UI workflow

1. **Planning** — Edit slices + network → **Solve PL** → optional Deploy.
2. **Short** — PS loop/once → updates PRBs and `demand`.
3. **Medium** — PM loop/once → resizes compute for current demand.

Application hook: `loop_application.py` (log-only default; no cluster apply yet).

## API summary

| Layer | Endpoints |
|---|---|
| PL | `POST /api/v1/pl/solve`, `/pl/apply`, `/pl/apply/stream` |
| PM | `POST /api/v1/pm/solve`, `/pm/loop/start`, `/pm/loop/stop`, `GET /pm/loop/status` |
| PS | `POST /api/v1/ps/solve`, `/ps/loop/start`, `/ps/loop/stop`, `GET /ps/loop/status` |
