# DynDNS server (central)

[benjaminbear/docker-ddns-server](https://github.com/benjaminbear/docker-ddns-server) on the **central** cluster: BIND-backed dynamic DNS with a Web UI.

Zone naming: **`<cluster>.inainfra`** (central → `central.inainfra`).

| | |
|---|---|
| VIP | `10.1.137.106` (`/32` on `central-0` `enp7s0`) |
| DNS | `10.1.137.106:53` (TCP/UDP hostPort) |
| Web UI | [http://10.1.137.106:8088](http://10.1.137.106:8088) (hostPort; container `:8080`) |
| Zone | `central.inainfra` |
| Parent NS name | `ns.central.inainfra` |
| Admin (htpasswd) | user `inainfra` / password `inainfra` |

Image: `bbaerthlein/docker-ddns-server`. UI hostPort is **8088** because `central-0` already has a process on `:8080`.

## GitOps

```bash
./scripts/setup_ddns_secondary_ips.sh central
./scripts/render_ddns_gitops.sh central
./bringup/03_push_to_git_repos/push_git_repos.sh central
./scripts/check-configsync.sh central
```

Manifests: `repos/central-repo/namespaces/ddns/`. Helpers: `CLUSTER_DDNS_*` in [scripts/cluster_lib.sh](../scripts/cluster_lib.sh).

## Parent DNS (Pi-hole)

Pi-hole on `mgmt-0` remains the lab resolver. For names under `central.inainfra` to resolve via this server, add NS delegation (and glue) pointing at the VIP — see the project [DNS setup](https://github.com/benjaminbear/docker-ddns-server#dns-setup). Static A glue is also listed in [services/etc-dnsmasq.d/99-nephio-static.conf](../services/etc-dnsmasq.d/99-nephio-static.conf):

```text
address=/ns.central.inainfra/10.1.137.106
address=/ddns.central.inainfra/10.1.137.106
```

Apply/reload that dnsmasq snippet on Pi-hole after editing.

## Host updates

After creating a hostname in the Web UI:

```text
http://inainfra:inainfra@10.1.137.106:8088/update?hostname=host.central.inainfra&myip=1.2.3.4
```

Also accepts `/nic/update`, `/v2/update`, `/v3/update` (see upstream README).

## Verify

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -u inainfra:inainfra http://10.1.137.106:8088/
dig @10.1.137.106 central.inainfra SOA +short
kubectl --context central@central -n ddns get deploy,pods,svc,pvc
```
