# Glass ISC DHCP (Kubernetes)

Site DHCP for `10.1.137.0/24` on **central** (`central-0` `enp7s0`, hostNetwork).

## Flow

```bash
# 1. Build + push images to 10.1.132.30:5000
./services/glass-dhcp/build_push.sh

# 2. Render GitOps manifests
./scripts/render_glass_dhcp_gitops.sh central

# 3. Push to Gitea → Config Sync
./bringup/03_push_to_git_repos/push_git_repos.sh -m 'Deploy Glass DHCP' central
```

## Verify

```bash
kubectl --context central@central -n glass-dhcp get pods -o wide
curl -s http://10.1.132.210:3000/api/get_server_info
```

Glass UI: http://10.1.132.210:3000  
- Username: `glassadmin`  
- Password: `glassadmin`  

Pool: `10.1.137.160–199` (see `dhcpd.conf`)
