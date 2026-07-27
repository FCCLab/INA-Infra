# INA-Infra GitOps templates (Jinja2)
#
# Rendered by backend gitea_apply into:
#   repos/{central,regional,edge}-repo/namespaces/<profile>/
#
# IP formula (see ip_allocator.py):
#   shared:  host = base[role]
#   slice:   host = base[role] + n   (n = 1..N)
#
# Layout mirrors oai-slice-deployment Multus NADs on a dedicated subnet
# (default 10.1.140.0/24). Dedicated core + RAN NFDeploy CRs can be added
# beside these NADs/ConfigMaps without changing the IP plan.
