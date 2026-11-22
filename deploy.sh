#!/usr/bin/env bash
# ============================================================================
# One-Command Customer Deployment Script (Any GCP Project)
# Usage:
#   export GCP_PROJECT_ID="your-gcp-project-id"
#   export GCP_REGION="us-central1"          # optional (default: us-central1)
#   export BQ_LOCATION="US"                  # optional (default: US)
#   export BQ_DATASET_ID="clinical_intelligence"
#   ./deploy.sh
# ============================================================================
set -euo pipefail

GCP_PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID before running deploy.sh}"
GCP_REGION="${GCP_REGION:-us-central1}"
BQ_LOCATION="${BQ_LOCATION:-US}"
BQ_DATASET_ID="${BQ_DATASET_ID:-clinical_intelligence}"
BQ_CONNECTION_ID="${GCP_PROJECT_ID}.${BQ_LOCATION}.vertex_docai_conn"
PRIVATE_PDFS_BUCKET="${GCP_PROJECT_ID}-private-medical-pdfs"

echo "==> [1/5] Provisioning base GCP infrastructure with Terraform..."
pushd terraform >/dev/null
terraform init -upgrade
terraform apply -auto-approve \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="region=${GCP_REGION}" \
  -var="bq_location=${BQ_LOCATION}" \
  -var="dataset_id=${BQ_DATASET_ID}"
ARTIFACT_REPO="$(terraform output -raw artifact_registry_repo)"
popd >/dev/null

echo "==> [2/5] Initializing Optimized BigQuery AI tables & stored procedures..."
run_bq_sql() {
  local sql_file="$1"
  echo "    Running ${sql_file}..."
  GCP_PROJECT_ID="${GCP_PROJECT_ID}" \
  BQ_DATASET_ID="${BQ_DATASET_ID}" \
  BQ_CONNECTION_ID="${BQ_CONNECTION_ID}" \
  PRIVATE_PDFS_BUCKET="${PRIVATE_PDFS_BUCKET}" \
  envsubst < "${sql_file}" | bq query --project_id="${GCP_PROJECT_ID}" --location="${BQ_LOCATION}" --use_legacy_sql=false
}

run_bq_sql sql/01_pubmed_optimized_tables.sql
run_bq_sql sql/03_pubmed_incremental_sync.sql
run_bq_sql sql/02_pubmed_indexes.sql
run_bq_sql sql/04_clinical_trials_schema.sql
run_bq_sql sql/05_patient_case_doc_intelligence.sql
run_bq_sql sql/06_private_pdfs_rag.sql
run_bq_sql sql/07_hybrid_search_and_hydrate.sql

echo "==> [3/5] Building & pushing container images via Cloud Build..."
TRIALS_IMAGE="${ARTIFACT_REPO}/clinical-trials-sync:latest"
APP_IMAGE="${ARTIFACT_REPO}/clinical-case-intelligence:latest"

gcloud builds submit ./ingestion --project="${GCP_PROJECT_ID}" --tag="${TRIALS_IMAGE}"
gcloud builds submit ./app --project="${GCP_PROJECT_ID}" --tag="${APP_IMAGE}"

echo "==> [4/5] Deploying Cloud Run Web App & ClinicalTrials.gov Sync Job..."
pushd terraform >/dev/null
terraform apply -auto-approve \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="region=${GCP_REGION}" \
  -var="bq_location=${BQ_LOCATION}" \
  -var="dataset_id=${BQ_DATASET_ID}" \
  -var="trials_sync_image=${TRIALS_IMAGE}" \
  -var="app_image=${APP_IMAGE}"
APP_URL="$(terraform output -raw app_url)"
popd >/dev/null

echo "==> [5/5] Triggering initial ClinicalTrials.gov v2 sync job..."
gcloud run jobs execute clinical-trials-incremental-sync \
  --project="${GCP_PROJECT_ID}" \
  --region="${GCP_REGION}" \
  --wait

echo ""
echo "============================================================================"
echo " Deployment Complete!"
echo " Clinical Case Intelligence UI: ${APP_URL}"
echo " BigQuery Dataset:              ${GCP_PROJECT_ID}.${BQ_DATASET_ID}"
echo "============================================================================"
