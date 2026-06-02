# network-config

## Description

network-config controller applies network configuration to network switches/routers.

See [network-config-operator](https://github.com/henderiw-nephio/network-config-operator).

## Apply (kpt)

Apply CRDs before the controller:

```bash
cd /home/fcp/nephio-network-slicing/bringup

kpt fn render network-config/crd
kpt live init network-config/crd    # first time only
kpt live apply network-config/crd --output=table

kpt fn render network-config
kpt live init network-config          # first time only
kpt live apply network-config --output=table
```

Or apply the full package tree from `network-config` root if your inventory includes both `crd` and `app`.

## Required CRD

The controller watches `config.nephio.org/v1alpha1` `Network` resources. The manifest is in `crd/bases/config.nephio.org_networks.yaml`.
