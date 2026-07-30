# Bring up central cluster (kubeadm)

> **Note:** This guide uses legacy lab IPs (`10.1.101.x`). The multi-site testbed uses **`10.1.132.0/24`** (mgmt) and **`10.1.137.0/24` / `10.1.138.0/24`** (site). See [bringup/00_testbed/readme.md](../bringup/00_testbed/readme.md) and [ip_plan.md](ip_plan.md).

Second Kubernetes cluster for OAI / workload workloads. The **management** cluster stays on `10.1.101.10` (see [readme.md](../readme.md)). This guide builds the **central** cluster on **`10.1.101.22`**.

| Cluster | User | Context | File |
|---------|------|---------|------|
| mgmt | `mgmt` | `mgmt@mgmt` | `~/.kube/config` |
| central | `central` | `central@central` | `~/.kube/config-central` |

Single export (user names match cluster names — no credential clash):

```bash
export KUBECONFIG=/home/fcp/.kube/config:/home/fcp/.kube/config-central
kubectl config use-context mgmt@mgmt
kubectl config use-context central@central
```

## Topology

| Cluster (kubeconfig name) | Host IP | Role |
|---------------------------|---------|------|
| mgmt | `10.1.101.10` | Nephio, Porch, Gitea, operators |
| central | `10.1.101.22` | Workload cluster (OAI NFs, database, etc.) |

## Networking (match mgmt)

| Layer | CIDR / address | Notes |
|-------|----------------|--------|
| **Pods** | `10.244.0.0/16` | Flannel (isolated per cluster; same CIDR is fine on a separate cluster) |
| **Services** | `10.96.0.0/12` | Default kubeadm service range |
| **API** | `https://10.1.101.22:6443` | `--apiserver-advertise-address` |

Open **TCP 6443** on `10.1.101.22` if you use `kubectl` from mgmt or your laptop.

---

## 1. Prep (on `10.1.101.22`)

Run as root or with `sudo`:

```bash
sudo swapoff -a
sudo sed -i '/ swap / s/^\(.*\)$/#\1/' /etc/fstab

sudo modprobe overlay br_netfilter

cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF

cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sudo sysctl --system
```

Install **containerd** (or your runtime), **kubelet**, **kubeadm**, and **kubectl** at the same minor version as mgmt (e.g. 1.30.x).

---

## 2. Init control plane

Single-node control plane:

```bash
sudo kubeadm init \
  --pod-network-cidr=10.244.0.0/16 \
  --apiserver-advertise-address=10.1.101.22
```

Save the `kubeadm join ...` output if you will add worker nodes later.

---

## 3. Central kubeconfig — copy from `admin.conf`

On **10.1.101.22**, copy kubeadm’s admin kubeconfig (do not overwrite mgmt `config` on a shared workstation):

```bash
mkdir -p $HOME/.kube
sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config-central
sudo chown $(id -u):$(id -g) $HOME/.kube/config-central
chmod 600 $HOME/.kube/config-central
export KUBECONFIG=$HOME/.kube/config-central
kubectl cluster-info
```

Optional rename (repo root on central host or mgmt):

```bash
./rename.sh central central $HOME/.kube/config-central
# context: central@central
```

---

## 4. Install Flannel CNI

On central / `10.1.101.22` (after section 3 — copy from `admin.conf`):

```bash
export KUBECONFIG=$HOME/.kube/config-central
kubectl apply -f https://github.com/flannel-io/flannel/releases/latest/download/kube-flannel.yml
```

Wait until the node is Ready:

```bash
kubectl get nodes
kubectl get pods -n kube-system
```

---

## 5. Copy central kubeconfig on mgmt host (`10.1.101.10`)

From **mgmt**, copy `admin.conf` from central (works as soon as `kubeadm init` finished on `.22`):

```bash
scp fcp@10.1.101.22:/etc/kubernetes/admin.conf /home/fcp/.kube/config-central
chmod 600 /home/fcp/.kube/config-central
./rename.sh central central /home/fcp/.kube/config-central
kubectl --kubeconfig=/home/fcp/.kube/config-central cluster-info
kubectl --kubeconfig=/home/fcp/.kube/config-central get nodes
```

Mgmt kubeconfig (same pattern, on **10.1.101.10**):

```bash
sudo cp -i /etc/kubernetes/admin.conf /home/fcp/.kube/config
sudo chown fcp:fcp /home/fcp/.kube/config
chmod 600 /home/fcp/.kube/config
./rename.sh mgmt mgmt /home/fcp/.kube/config
```

Switch cluster:

```bash
export KUBECONFIG=/home/fcp/.kube/config:/home/fcp/.kube/config-central
kubectl config use-context mgmt@mgmt
kubectl get nodes

kubectl config use-context central@central
kubectl get nodes
```

---

## Verify nodes

```bash
kubectl --context=mgmt@mgmt get nodes -o wide
kubectl --context=central@central get nodes -o wide
```

Expected: one node `Ready` per cluster; Flannel and CoreDNS running in `kube-system`.

---

## Pods and namespaces per cluster

```bash
export KUBECONFIG=/home/fcp/.kube/config:/home/fcp/.kube/config-central

kubectl --context=mgmt@mgmt get ns
kubectl --context=mgmt@mgmt get pods -A

kubectl --context=central@central get ns
kubectl --context=central@central get pods -A
```

---

## Notes

- This only creates Kubernetes on **central** (`10.1.101.22`); it does not install Nephio, Porch, or Gitea (those stay on **mgmt** unless you choose to deploy more there).
- Register **central** with Nephio (e.g. `WorkloadCluster`, Git repo, Config Sync) separately when you wire mgmt → central GitOps.
- **Mgmt** init reference: `sudo kubeadm init --pod-network-cidr=10.244.0.0/16 --apiserver-advertise-address=10.1.101.10` in [readme.md](../readme.md).
