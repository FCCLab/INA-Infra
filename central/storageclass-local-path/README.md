# storageclass-local-path

## Description

KPT package that installs the Rancher local-path provisioner and a default `local-path` StorageClass.

Apply this package before workloads that use PVCs (for example `bringup/gitea`).

## Apply (kpt only)

```bash
cd /home/fcp/nephio-network-slicing/bringup

kpt fn render storageclass-local-path
kpt live init storageclass-local-path   # first time only
kpt live apply storageclass-local-path --output=table
```

Verify:

```bash
kubectl get storageclass
kubectl -n local-path-storage get pods
```

## Destroy (kpt only)

```bash
kpt live destroy storageclass-local-path
```

Note: destroying removes dynamic volumes created by this provisioner.
