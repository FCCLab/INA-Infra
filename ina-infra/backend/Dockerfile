# Build from monorepo root:
#   docker build -f ina-infra/backend/Dockerfile -t ina-infra-backend:latest .
FROM python:3.10-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash git ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY ina-infra/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Keep host layout under /app/ina-infra so backend/scripts/lib.sh resolves roots.
COPY ina-infra/backend/app /app/ina-infra/backend/app
COPY ina-infra/backend/scripts /app/ina-infra/backend/scripts
COPY ina-infra/templates /app/ina-infra/templates
COPY ina-infra/oai-controller-base /app/ina-infra/oai-controller-base
COPY ina-infra/oai-controller-utils /app/ina-infra/oai-controller-utils
COPY algorithm/new_implementation /app/ina_src

ENV PYTHONPATH=/app/ina-infra/backend
ENV INA_SRC=/app/ina_src
ENV INA_INFRA_ROOT=/app/ina-infra
ENV PUSH_SCRIPT=/app/ina-infra/backend/scripts/push_gitea_gitops.sh
ENV SSH_CFG=/app/ina-infra/backend/scripts/ssh_config
ENV GRB_LICENSE_FILE=/opt/gurobi/gurobi.lic
ENV PYTHONUNBUFFERED=1

WORKDIR /app/ina-infra/backend
EXPOSE 8082

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082"]
