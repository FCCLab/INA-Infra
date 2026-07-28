# OAI controller base manifests

Static SA / Deployment / op-conf / nf-conf YAML used by
`scripts/render_ina_cn_operators_gitops.sh` to emit profile-scoped controllers
into `repos/*/namespaces/<profile>/70-*`.

Utils overlays: `../oai-controller-utils/`.
Do not edit live GitOps under `repos/*/namespaces/oai-cn-operators` — that ns is retired.
