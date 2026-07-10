# Configuring an Ubuntu System to Trust the Secure Registry

The local Docker Registry is hosted on the `mgmt` cluster at **`https://10.1.132.30:5000`** (DNS: `registry.nephio.lab`) and uses a self-signed TLS certificate. 

To enable an existing Ubuntu host or Kubernetes node to push/pull images from this registry, configure Docker or Containerd to trust it using the instructions below.

---

## 1. Configure Docker Engine

On an Ubuntu system running Docker, use either of the following methods to trust the self-signed registry certificate.

### Method A: Configure as an Insecure Registry (Recommended)
This tells the Docker daemon to skip SSL certificate verification for this specific endpoint.

1. Edit or create `/etc/docker/daemon.json`:
   ```bash
   sudo nano /etc/docker/daemon.json
   ```
2. Add the registry address to the `insecure-registries` array:
   ```json
   {
     "insecure-registries": [
       "10.1.132.30:5000",
       "registry.nephio.lab:5000"
     ]
   }
   ```
3. Restart the Docker daemon to apply changes:
   ```bash
   sudo systemctl restart docker
   ```
4. Verify the configuration:
   ```bash
   docker info 2>/dev/null | grep -A 3 "Insecure Registries"
   ```

### Method B: Install the CA Certificate
Alternatively, install the registry's root CA certificate directly into Docker's configuration directory so that it validates the certificate securely.

1. Fetch or copy the registry's certificate (`registry.crt`) from the host or secret.
2. Create the Docker certificates directories:
   ```bash
   sudo mkdir -p /etc/docker/certs.d/10.1.132.30:5000
   sudo mkdir -p /etc/docker/certs.d/registry.nephio.lab:5000
   ```
3. Copy/Save the `registry.crt` certificate file as `ca.crt` inside both directories:
   ```bash
   sudo cp registry.crt /etc/docker/certs.d/10.1.132.30:5000/ca.crt
   sudo cp registry.crt /etc/docker/certs.d/registry.nephio.lab:5000/ca.crt
   ```
   *Docker automatically picks up these certs without requiring a daemon restart.*

---

## 2. Configure Containerd (Kubernetes Nodes)

Containerd uses a directory-based registry configuration layout (`certs.d`) starting in v1.5+.

1. Ensure containerd is configured to read from `certs.d`. Check `/etc/containerd/config.toml` (or equivalent) for the `config_path` setting:
   ```toml
   [plugins."io.containerd.grpc.v1.cri".registry]
     config_path = "/etc/containerd/certs.d"
   ```
   *Note: If your nodes use CRI v1, the plugins path might be `[plugins."io.containerd.cri.v1.images".registry]`.*

2. Create the configuration directories for our registry:
   ```bash
   sudo mkdir -p /etc/containerd/certs.d/10.1.132.30:5000
   sudo mkdir -p /etc/containerd/certs.d/registry.nephio.lab:5000
   ```

3. Configure `/etc/containerd/certs.d/10.1.132.30:5000/hosts.toml`:
   ```bash
   sudo tee /etc/containerd/certs.d/10.1.132.30:5000/hosts.toml <<'EOF'
   server = "https://10.1.132.30:5000"
   
   [host."https://10.1.132.30:5000"]
     capabilities = ["pull", "resolve", "push"]
     skip_verify = true
   EOF
   ```

4. Configure `/etc/containerd/certs.d/registry.nephio.lab:5000/hosts.toml`:
   ```bash
   sudo tee /etc/containerd/certs.d/registry.nephio.lab:5000/hosts.toml <<'EOF'
   server = "https://registry.nephio.lab:5000"
   
   [host."https://registry.nephio.lab:5000"]
     capabilities = ["pull", "resolve", "push"]
     skip_verify = true
   EOF
   ```

5. Restart containerd on the node:
   ```bash
   sudo systemctl restart containerd
   ```

---

## 3. Registry Disk Capacity & Storage Usage

The Docker Registry in the `mgmt` cluster uses a PersistentVolumeClaim (PVC) backed by local host storage (`local-path` storage class).

### Capacity Metrics
* **Requested Claim Capacity**: `10Gi` (defined in `pvc-registry.yaml`)
* **Host Free Space**: The volume is hosted on a dedicated `2.0 Terabytes` disk partition (`/dev/vdb`) mounted on `node-0` at `/opt/local-path-provisioner`.
  * **Current Host Disk Available**: `1.9 TiB` (out of `2.0 TiB` total filesystem capacity).
* **Current Storage Utilized by Images**: `~176 KiB` (clean/fresh state with test images only).

### Checking Usage
To check the current storage size of the registry directories dynamically, run:
```bash
kubectl exec deployment/registry -n registry -c registry -- du -sh /var/lib/registry
```

To check the physical host disk utilization of the mounted volume:
```bash
kubectl exec deployment/registry -n registry -c registry -- df -h /var/lib/registry
```

---

## 4. Pushing OAI Images (Multi-Architecture Support)

To tag and push OAI latest images from remote host nodes (such as ARM64 and AMD64 hosts) to the local secure registry, use the `push_oai.sh` utility script. 

When multiple architecture hosts are provided, the script automatically creates a local manifest list and pushes it to the registry. This enables Kubernetes nodes (e.g. `gh81` running ARM64 or `usrp` running AMD64) to autonomously pull the correct image architecture.

### Usage

```bash
./utils/registry/push_oai.sh --arch <arch1> <host1> [--arch <arch2> <host2> ...] --version <version>
```

### Examples

* **Pushing both arm64 and amd64 images (creates multi-arch manifests in registry):**
  ```bash
  ./utils/registry/push_oai.sh --arch arm64 gh81 --arch amd64 usrp --version nws-v0.2
  ```

* **Pushing only arm64 architecture:**
  ```bash
  ./utils/registry/push_oai.sh --arch arm64 gh81 --version nws-v0.2
  ```

After push, `push_oai.sh` runs `verify_registry_image.sh` on each tag to confirm blob payloads are present (not just manifests). To check a tag manually:

```bash
./utils/registry/verify_registry_image.sh oai-nr-ue:nws-v0.2
./utils/registry/verify_registry_image.sh oai-gnb:nws-v0.2
```

