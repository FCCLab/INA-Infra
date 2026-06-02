# gitea

## Description

Gitea package to deploy a gitea server in a gitea namespace

## Prerequisites

Apply storage first (once per cluster):

```bash
cd /home/fcp/nephio-network-slicing/bringup
kpt fn render storageclass-local-path
kpt live init storageclass-local-path   # first time only
kpt live apply storageclass-local-path --output=table
```

## Apply

```bash
kpt fn render gitea
kpt live init gitea                     # first time only
kpt live apply gitea --output=table
```

## Database configuration

- PostgreSQL creates user `gitea` on first PVC init (`POSTGRES_USER=gitea` in `statefulset-postgres.yaml`).
- Database password must match in `secret-postgresql.yaml` (`password` key) and `secret-gitea-inline-config.yaml` (`PASSWD`, used when `app.ini` is generated).
- `GITEA__database__PASSWD` env vars on Gitea containers provide a runtime override from `secret-postgresql`.
- `wait-for-postgresql` init container blocks Gitea setup until user `gitea` can connect.

## Destroy

```bash
kpt live destroy gitea
```

Do not run `kpt live init` again on an already-initialized package (inventory mismatch).

## Server URL

Gitea `DOMAIN` / `ROOT_URL` use the in-cluster service DNS name (`gitea.gitea.svc.cluster.local`) so startup does not fail on unresolved `git.example.com`.

For browser access via LoadBalancer/NodePort, use your cluster endpoint (for example MetalLB `172.18.0.200:3000` from `service-gitea.yaml`) and update `ROOT_URL` if needed.

## Troubleshooting

If PostgreSQL was interrupted during first boot, delete PVCs once and re-apply:

```bash
kpt live destroy gitea
kubectl -n gitea delete pvc data-gitea-postgresql-0 data-gitea-0 --ignore-not-found
kpt live apply gitea --output=table
```

# OpenShift

When deploying this kpt package on OpenShift, you have to supply a specific SecurityContext because Gitea expect a specific `fsGroup` and `uid` that aren't in the tolerated range of OpenShift. To do so, apply the following manifests, that will create an SCC, a Role and a RoleBinding.

```
echo "kind: SecurityContextConstraints
metadata:
  name: gitea
allowHostDirVolumePlugin: false
allowHostIPC: false
allowHostNetwork: false
allowHostPID: false
allowHostPorts: false
allowPrivilegeEscalation: true
allowPrivilegedContainer: false
allowedCapabilities: null
apiVersion: security.openshift.io/v1
defaultAddCapabilities: null
fsGroup:
  type: RunAsAny
priority: 10
readOnlyRootFilesystem: false
requiredDropCapabilities:
- MKNOD
runAsUser:
  type: RunAsAny
seLinuxContext:
  type: MustRunAs
supplementalGroups:
  type: RunAsAny
users: []
volumes:
- configMap
- downwardAPI
- emptyDir
- persistentVolumeClaim
- projected
- secret
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: gitea-role
  namespace: gitea
rules:
- apiGroups:
  - security.openshift.io 
  resourceNames:
  - gitea
  resources:
  - securitycontextconstraints 
  verbs: 
  - use
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: gitea-rolebinding
  namespace: gitea
subjects:
- kind: ServiceAccount
  name: default
roleRef:
  kind: Role
  name: gitea-role
  apiGroup: rbac.authorization.k8s.io
" | kubectl apply -f -
```