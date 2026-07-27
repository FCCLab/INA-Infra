# INA-Infra new implementation

Slice-centric API: each layer takes a **list of `Slice` objects** (properties on the slice), not global config + id lists.

## API sketch

```python
from ina import PlanningLayer, MediumLayer, ShortLayer, Slice

slices = [
    Slice(id=1, t_bar=40, d_bar=100, h_s=0, eta_t0=3.0),
    Slice(id=2, t_bar=60, d_bar=10, h_s=1, eta_t0=2.5, slice_type="URLLC"),
]

pl = PlanningLayer().solve(slices)          # uses t_bar, d_bar, h_s, eta_t0
# slices[i].placement / .resources filled

for s in slices:
    s.demand = 45.0
MediumLayer().solve(slices)                # uses demand + placement

for s in slices:
    s.eta = 2.8
ps = ShortLayer().solve(slices)            # → b_min, b_ded, b_max(=b_min+extra)
```

## Layout

```text
ina/
  models.py      # Slice, SliceResources, PlResult
  network.py     # Network (DC capacities / costs / delays)
  layer1_pl.py   # PlanningLayer.solve(slices)
  layer2_pm.py   # MediumLayer.solve(slices)
  layer3_ps.py   # ShortLayer.solve(slices)
  eta.py, slices.py
simulation.py
examples/
tests/
```

`Network` is the physical substrate (optional; defaults are fine). Slice SLAs are **not** in a config — they live on each `Slice`.

## Run

```bash
cd algorithm/INA-Infra/new_implementation
python3 examples/sample_layer1_pl.py
python3 -m pytest tests/ -v

# Full sim + same 5 plots as classic simulation.py
python3 simulation.py

# Headless (save PNGs only under sim_output/)
python3 simulation.py --no-show --save-dir sim_output
```
