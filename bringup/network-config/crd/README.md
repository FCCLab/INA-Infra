# crd

## Description

CRDs required by the network-config controller.

## Contents

- `bases/config.nephio.org_networks.yaml` — `Network` (`config.nephio.org/v1alpha1`)

## Apply

```bash
kpt fn render /home/fcp/nephio-network-slicing/bringup/network-config/crd
kpt live init /home/fcp/nephio-network-slicing/bringup/network-config/crd
kpt live apply /home/fcp/nephio-network-slicing/bringup/network-config/crd --output=table
```

Apply this package before `network-config/app`.
