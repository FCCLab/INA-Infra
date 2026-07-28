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
# (default 10.1.140.0/24).
#
# On central, Apply also emits a dedicated control-plane stack into the
# profile namespace (MySQL + NRF/AUSF/UDM/UDR/AMF/SMF NFDeployments).
# OAI Kopf controllers co-locate in the same namespaces/<profile>/ dir (70-*;
# render_ina_cn_operators_gitops.sh ← ina-infra/oai-controller-base).
