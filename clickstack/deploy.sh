#!/usr/bin/env bash
# Deploy the ClickStack ingestion path into the SREGym cluster. Run on the runner with a
# working kubeconfig. Credentials come from a private env file, never from this repo.
set -euo pipefail
cd "$(dirname "$0")"
ENV_FILE="${CLICKSTACK_ENV_FILE:-$HOME/.config/sregym/clickstack.env}"
set -a; . "$ENV_FILE"; set +a

kubectl create namespace otel --dry-run=client -o yaml | kubectl apply -f -
kubectl -n otel create secret generic clickstack-credentials \
  --from-literal=CLICKHOUSE_ENDPOINT="$CLICKHOUSE_ENDPOINT" \
  --from-literal=CLICKHOUSE_USER="$CLICKHOUSE_INGEST_USER" \
  --from-literal=CLICKHOUSE_PASSWORD="$CLICKHOUSE_INGEST_PASSWORD" \
  --from-literal=OTLP_AUTH_TOKEN="$OTLP_AUTH_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

DB="${CLICKSTACK_DATABASE:-default}"   # one database per cluster when several clusters share the ClickHouse service
sed -E "s/^([[:space:]]*value: )\"default\"[[:space:]]*$/\1\"${DB}\"/" k8s/clickstack-collector.yaml | kubectl apply -f -
kubectl apply -f k8s/gateway.yaml
kubectl apply -f k8s/node-state.yaml
kubectl -n otel rollout restart deploy/otel-gateway >/dev/null

helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null 2>&1 || true
helm repo update open-telemetry >/dev/null
helm upgrade --install otel-node open-telemetry/opentelemetry-collector -n otel -f helm/daemonset-values.yaml
helm upgrade --install otel-cluster open-telemetry/opentelemetry-collector -n otel -f helm/deployment-values.yaml

kubectl -n otel rollout status deploy/clickstack-collector --timeout=240s
kubectl -n otel rollout status deploy/otel-gateway --timeout=240s
kubectl -n otel rollout status ds/otel-node-agent --timeout=240s
kubectl -n otel rollout status deploy/otel-cluster --timeout=240s
kubectl -n otel rollout status ds/node-state --timeout=240s
kubectl -n otel get pods -o wide
