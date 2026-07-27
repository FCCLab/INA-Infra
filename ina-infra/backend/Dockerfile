# Build from monorepo root:
#   docker build -f ina-infra/backend/Dockerfile -t ina-infra-backend:latest .
FROM python:3.10-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY ina-infra/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ina-infra/backend/app ./app
COPY algorithm/new_implementation /app/ina_src

ENV PYTHONPATH=/app
ENV INA_SRC=/app/ina_src
ENV GRB_LICENSE_FILE=/opt/gurobi/gurobi.lic
ENV PYTHONUNBUFFERED=1

EXPOSE 8082

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8082"]
