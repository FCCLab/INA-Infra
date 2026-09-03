# Experiment 1: Benefits of Long-Term Planning (PL)

## 1. Overview
This directory contains the real multi-cluster testbed GitOps deployment, measurement, and publication plotting pipeline for **Experiment Set 1: Benefits of Long-Term Planning (PL)** using dedicated Kubernetes namespaces for each test scheme.

---

## 2. Dedicated Namespaces & Schemes

| Namespace | Scheme Name | Multi-Cluster Physical Placement | Deployment Script |
| :--- | :--- | :--- | :--- |
| **`exp1-a`** | **Full Algorithm (Proposed PL)** | Dynamic tier placement: Physical-AI at Edge (`gpu-a40`), CCTV at Regional (`gpu-gh82`), OTT & IoT at Central (`gpu-gh81`). | [`exp1_a_deploy.py`](file:///home/fcp/INA-Infra/paper/exp1/exp1_a_deploy.py) |
| **`exp1-b`** | **Fixed Edge Baseline** | All Slices (1, 2, 3, 4) CU-UP, UPF, and App servers deployed strictly at Edge (`edge@edge`). | [`exp1_b_deploy.py`](file:///home/fcp/INA-Infra/paper/exp1/exp1_b_deploy.py) |
| **`exp1-c`** | **Fixed Regional Baseline** | All Slices (1, 2, 3, 4) deployed strictly at Regional (`regional@regional`). | [`exp1_c_deploy.py`](file:///home/fcp/INA-Infra/paper/exp1/exp1_c_deploy.py) |
| **`exp1-d`** | **Fixed Central Baseline** | All Slices (1, 2, 3, 4) deployed strictly at Central (`central@central`). | [`exp1_d_deploy.py`](file:///home/fcp/INA-Infra/paper/exp1/exp1_d_deploy.py) |

---

## 3. Directory Structure

```
paper/exp1/
├── README.md                      # Experiment documentation and procedures
├── generate_exp1_gitops.py        # Core GitOps generator & multi-cluster deployer
├── exp1_a_deploy.py               # Deploy Scheme A (Proposed PL) -> namespace exp1-a
├── exp1_b_deploy.py               # Deploy Scheme B (Fixed Edge) -> namespace exp1-b
├── exp1_c_deploy.py               # Deploy Scheme C (Fixed Regional) -> namespace exp1-c
├── exp1_d_deploy.py               # Deploy Scheme D (Fixed Central) -> namespace exp1-d
├── run_exp1_testbed.py            # Real multi-cluster testbed measurement runner
├── simulate_exp1_pl.py            # Large-scale theoretical PL numerical solver
├── plot_exp1.py                   # Publication figure generator
├── gitops_manifests/              # Generated GitOps packages per dedicated namespace
│   ├── exp1-a/                    # Manifests for namespace exp1-a (Proposed PL)
│   ├── exp1-b/                    # Manifests for namespace exp1-b (Fixed Edge)
│   ├── exp1-c/                    # Manifests for namespace exp1-c (Fixed Regional)
│   └── exp1-d/                    # Manifests for namespace exp1-d (Fixed Central)
├── data/                          # Real measured & simulated CSV datasets
│   ├── exp1_testbed_results.csv
│   ├── exp1_latency_breakdown.csv
│   └── exp1_sim_results.csv
└── plots/                         # Publication-ready PNG figures
    ├── fig1a_opex_vs_sla_pareto.png
    ├── fig1b_e2e_latency_breakdown.png
    └── fig1c_testbed_vs_simulation.png
```

---

## 4. Execution Workflow

### Step 1: Deploy Scheme of Choice to Its Dedicated Namespace
Run any of the dedicated deployment scripts to render, commit, and push to Gitea for Google ConfigSync:
```bash
# To deploy Scheme A (Proposed PL) to namespace exp1-a:
python3 paper/exp1/exp1_a_deploy.py

# To deploy Scheme B (Fixed Edge) to namespace exp1-b:
python3 paper/exp1/exp1_b_deploy.py

# To deploy Scheme C (Fixed Regional) to namespace exp1-c:
python3 paper/exp1/exp1_c_deploy.py

# To deploy Scheme D (Fixed Central) to namespace exp1-d:
python3 paper/exp1/exp1_d_deploy.py
```

### Step 2: Run Multi-Cluster Testbed Measurement
Executes live cluster probing (Edge, Regional, Central hops), computes real OPEX, and logs SLA satisfaction:
```bash
python3 paper/exp1/run_exp1_testbed.py
```

### Step 3: Run Analytical Simulation Sweep
Solves the Layer 1 PL optimization model across continuous SLA reliability targets:
```bash
python3 paper/exp1/simulate_exp1_pl.py
```

### Step 4: Generate Publication Figures
Produces all 3 publication figures in `plots/`:
```bash
python3 paper/exp1/plot_exp1.py
```

---

## 5. Undeployment Scripts

To tear down UEs, workloads, and namespaces cleanly:

### Undeploy Scheme A (exp1-a)
```bash
# Undeploy both UEs and server infrastructure for Scheme A:
python3 paper/exp1/exp1_a_undeploy.py

# Undeploy only UEs and application clients from Edge cluster:
python3 paper/exp1/exp1_a_undeploy_ue.py
```

### Multi-Scheme Unified Undeployer
```bash
# Undeploy specific scheme (exp1-a, exp1-b, exp1-c, or exp1-d):
python3 paper/exp1/exp1_undeploy.py --scheme exp1-a

# Undeploy all experiment schemes from all clusters:
python3 paper/exp1/exp1_undeploy.py --all

# Clean up only UEs without deleting server infrastructure:
python3 paper/exp1/exp1_undeploy.py --scheme exp1-a --ue-only
```

