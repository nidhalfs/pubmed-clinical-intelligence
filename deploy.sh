#!/usr/bin/env bash
# ============================================================================
# One-Command Customer Deployment Script (Any GCP Project)
# Works out-of-the-box in Cloud Shell (with or without Terraform installed!).
#
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
BQ_CONN_NAME="vertex_docai_conn"
BQ_CONNECTION_ID="${GCP_PROJECT_ID}.${BQ_LOCATION}.${BQ_CONN_NAME}"
PATIENT_DOCS_BUCKET="${GCP_PROJECT_ID}-patient-case-docs"
PRIVATE_PDFS_BUCKET="${GCP_PROJECT_ID}-private-medical-pdfs"
ARTIFACT_REPO_NAME="clinical-intelligence"
ARTIFACT_REPO="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${ARTIFACT_REPO_NAME}"
APP_SA_NAME="clinical-intel-runner"
APP_SA_EMAIL="${APP_SA_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

HAS_TERRAFORM=false
if command -v terraform >/dev/null 2>&1 && terraform version 2>&1 | grep -q "^Terraform v"; then
  HAS_TERRAFORM=true
fi

echo "==> [1/5] Provisioning base GCP infrastructure..."
if [ "${HAS_TERRAFORM}" = true ]; then
  echo "    Using Terraform..."
  pushd terraform >/dev/null
  terraform init -upgrade
  terraform apply -auto-approve \
    -var="project_id=${GCP_PROJECT_ID}" \
    -var="region=${GCP_REGION}" \
    -var="bq_location=${BQ_LOCATION}" \
    -var="dataset_id=${BQ_DATASET_ID}"
  ARTIFACT_REPO="$(terraform output -raw artifact_registry_repo)"
  popd >/dev/null
else
  echo "    Terraform not installed (Cloud Shell stub detected); provisioning natively with gcloud & bq..."
  gcloud services enable \
    bigquery.googleapis.com \
    bigqueryconnection.googleapis.com \
    aiplatform.googleapis.com \
    documentai.googleapis.com \
    storage.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    iam.googleapis.com \
    --project="${GCP_PROJECT_ID}"

  # Artifact Registry repository
  gcloud artifacts repositories describe "${ARTIFACT_REPO_NAME}" \
    --location="${GCP_REGION}" --project="${GCP_PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "${ARTIFACT_REPO_NAME}" \
    --repository-format=docker \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT_ID}"

  # GCS Buckets
  gcloud storage buckets describe "gs://${PATIENT_DOCS_BUCKET}" --project="${GCP_PROJECT_ID}" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${PATIENT_DOCS_BUCKET}" --project="${GCP_PROJECT_ID}" --location="${BQ_LOCATION}" --uniform-bucket-level-access
  gcloud storage buckets describe "gs://${PRIVATE_PDFS_BUCKET}" --project="${GCP_PROJECT_ID}" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${PRIVATE_PDFS_BUCKET}" --project="${GCP_PROJECT_ID}" --location="${BQ_LOCATION}" --uniform-bucket-level-access

  # BigQuery Dataset
  bq --project_id="${GCP_PROJECT_ID}" show --dataset "${GCP_PROJECT_ID}:${BQ_DATASET_ID}" >/dev/null 2>&1 || \
    bq --project_id="${GCP_PROJECT_ID}" mk --location="${BQ_LOCATION}" --dataset "${GCP_PROJECT_ID}:${BQ_DATASET_ID}"

  # BigQuery Cloud Resource Connection (for AI.PARSE_DOCUMENT, AI.CHUNK_DOCUMENT, AI.EMBED, AI.GENERATE)
  bq --project_id="${GCP_PROJECT_ID}" show --connection "${BQ_CONNECTION_ID}" >/dev/null 2>&1 || \
    bq --project_id="${GCP_PROJECT_ID}" mk --connection --location="${BQ_LOCATION}" --connection_type=CLOUD_RESOURCE "${BQ_CONN_NAME}"

  BQ_CONN_SA="$(bq --project_id="${GCP_PROJECT_ID}" show --format=json --connection "${BQ_CONNECTION_ID}" | python3 -c "import json,sys; print(json.load(sys.stdin)['cloudResource']['serviceAccountId'])")"
  echo "    BigQuery Connection SA: ${BQ_CONN_SA}"

  for role in roles/documentai.editor roles/aiplatform.user roles/storage.objectViewer; do
    gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
      --member="serviceAccount:${BQ_CONN_SA}" \
      --role="${role}" --condition=None --quiet >/dev/null
  done

  # Dedicated App & Sync Job Service Account
  gcloud iam service-accounts describe "${APP_SA_EMAIL}" --project="${GCP_PROJECT_ID}" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "${APP_SA_NAME}" \
      --display-name="Clinical Case Intelligence Runner SA" \
      --project="${GCP_PROJECT_ID}"

  for role in roles/bigquery.jobUser roles/bigquery.dataEditor roles/bigquery.connectionUser roles/aiplatform.user roles/documentai.editor roles/storage.objectAdmin; do
    gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
      --member="serviceAccount:${APP_SA_EMAIL}" \
      --role="${role}" --condition=None --quiet >/dev/null
  done

  echo "    Waiting 10s for IAM & BigQuery Connection propagation..."
  sleep 10
fi

echo "==> [2/5] Initializing Optimized BigQuery AI tables & stored procedures..."
bq --project_id="${GCP_PROJECT_ID}" show --dataset "${GCP_PROJECT_ID}:${BQ_DATASET_ID}" >/dev/null 2>&1 || \
  bq --project_id="${GCP_PROJECT_ID}" mk --location="${BQ_LOCATION}" --dataset "${GCP_PROJECT_ID}:${BQ_DATASET_ID}"

run_bq_sql() {
  local sql_file="$1"
  echo "    Running ${sql_file}..."
  GCP_PROJECT_ID="${GCP_PROJECT_ID}" \
  BQ_LOCATION="${BQ_LOCATION}" \
  BQ_DATASET_ID="${BQ_DATASET_ID}" \
  BQ_CONNECTION_ID="${BQ_CONNECTION_ID}" \
  PRIVATE_PDFS_BUCKET="${PRIVATE_PDFS_BUCKET}" \
  envsubst < "${sql_file}" | bq query --project_id="${GCP_PROJECT_ID}" --location="${BQ_LOCATION}" --use_legacy_sql=false
}

run_bq_sql sql/01_pubmed_optimized_tables.sql
if [ "${SKIP_PUBMED_SYNC:-false}" != "true" ]; then
  run_bq_sql sql/03_pubmed_incremental_sync.sql
fi
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
if [ "${HAS_TERRAFORM}" = true ]; then
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
else
  gcloud run jobs deploy clinical-trials-incremental-sync \
    --project="${GCP_PROJECT_ID}" \
    --region="${GCP_REGION}" \
    --image="${TRIALS_IMAGE}" \
    --service-account="${APP_SA_EMAIL}" \
    --cpu=2 --memory=4Gi --task-timeout=1800s \
    --set-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},BQ_DATASET_ID=${BQ_DATASET_ID},LOOKBACK_DAYS=30"

  gcloud run deploy clinical-case-intelligence \
    --project="${GCP_PROJECT_ID}" \
    --region="${GCP_REGION}" \
    --image="${APP_IMAGE}" \
    --service-account="${APP_SA_EMAIL}" \
    --cpu=2 --memory=4Gi --timeout=600s \
    --no-allow-unauthenticated \
    --set-env-vars="GCP_PROJECT_ID=${GCP_PROJECT_ID},GCP_REGION=${GCP_REGION},BQ_LOCATION=${BQ_LOCATION},BQ_DATASET_ID=${BQ_DATASET_ID},BQ_CONNECTION_ID=${BQ_CONNECTION_ID},PATIENT_DOCS_BUCKET=${PATIENT_DOCS_BUCKET},PRIVATE_PDFS_BUCKET=${PRIVATE_PDFS_BUCKET}"

  APP_URL="$(gcloud run services describe clinical-case-intelligence --project="${GCP_PROJECT_ID}" --region="${GCP_REGION}" --format='value(status.url)')"
fi

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
echo " Access securely via:"
echo "   gcloud run services proxy clinical-case-intelligence --region=${GCP_REGION} --project=${GCP_PROJECT_ID}"
echo "============================================================================"
