# Bring up Nephio

```
kpt fn render network-config
kpt live init network-config
kpt live apply network-config --reconcile-timeout=15m --output=table
```

```
kpt fn render resource-backend
kpt live init resource-backend
kpt live apply resource-backend --reconcile-timeout=15m --output=table
```

```
kpt fn render storageclass-local-path
kpt live init storageclass-local-path    # first time only
kpt live apply storageclass-local-path --output=table
```

```
kpt fn render gitea
kpt live init gitea
kpt live apply gitea --reconcile-timeout 15m --output=table
```
