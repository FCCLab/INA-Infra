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

```
kpt pkg get --for-deployment https://github.com/nephio-project/catalog/nephio/core/porch@origin/main
kpt fn render porch
kpt live init porch
kpt live apply porch --reconcile-timeout=15m --output=table
```

```
kpt pkg get --for-deployment https://github.com/nephio-project/catalog.git/nephio/core/nephio-operator@origin/main
kpt fn render nephio-operator
kpt live init nephio-operator
kpt live apply nephio-operator --reconcile-timeout=15m --output=table
```

```
kubectl apply -f  - <<EOF
apiVersion: v1
kind: Secret
metadata:
    name: git-user-secret
    namespace: nephio-system
type: kubernetes.io/basic-auth
stringData:
    username: nephio
    password: secret
EOF

kubectl -n nephio-system get secret git-user-secret
kubectl -n nephio-system get secret git-user-secret -o jsonpath='{.data.username}' | base64 -d; echo
kubectl -n nephio-system get secret git-user-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

```
kpt pkg get --for-deployment https://github.com/nephio-project/catalog.git/nephio/optional/stock-repos@origin/main
kpt fn render stock-repos
kpt live init stock-repos
kpt live apply stock-repos --reconcile-timeout=15m --output=table
```
