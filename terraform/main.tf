terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.30.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  required_apis = [
    "bigquery.googleapis.com",
    "bigqueryconnection.googleapis.com",
    "bigquerydatatransfer.googleapis.com",
    "aiplatform.googleapis.com",
    "documentai.googleapis.com",
    "storage.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
  ]
}

# 1. Enable required GCP APIs
resource "google_project_service" "apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# 2. Artifact Registry for customer-built Cloud Run images
resource "google_artifact_registry_repository" "repo" {
  location      = var.region
  repository_id = "clinical-intelligence"
  format        = "DOCKER"
  description   = "Container images for PubMed & ClinicalTrials Case Intelligence"
  depends_on    = [google_project_service.apis]
}

# 3. Cloud Storage Buckets
# 3a. Patient case documents (PDF/DOCX/PPTX uploaded by clinicians)
resource "google_storage_bucket" "patient_docs" {
  name                        = "${var.project_id}-patient-case-docs"
  location                    = var.bq_location
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# 3b. Optional Customer Private Literature PDFs (institutional protocols, internal case series)
resource "google_storage_bucket" "private_pdfs" {
  name                        = "${var.project_id}-private-medical-pdfs"
  location                    = var.bq_location
  uniform_bucket_level_access = true
  force_destroy               = false

  depends_on = [google_project_service.apis]
}

# 4. BigQuery Dataset (Optimized PubMed + ClinicalTrials + Patient Case Intelligence)
resource "google_bigquery_dataset" "clinical_intelligence" {
  project     = var.project_id
  dataset_id  = var.dataset_id
  location    = var.bq_location
  description = "Optimized PubMed & ClinicalTrials vector/hybrid search layer + AI.PARSE_DOCUMENT / AI.CHUNK_DOCUMENT patient case intelligence"
  depends_on  = [google_project_service.apis]
}

# 5. BigQuery Cloud Resource Connection (used by AI.PARSE_DOCUMENT, AI.CHUNK_DOCUMENT, AI.EMBED, AI.GENERATE)
resource "google_bigquery_connection" "vertex_docai_conn" {
  project       = var.project_id
  connection_id = var.bq_connection_id
  location      = var.bq_location
  description   = "Cloud Resource connection for BigQuery AI Document Intelligence and Vertex AI models"
  cloud_resource {}
  depends_on = [google_project_service.apis]
}

# Grant the BigQuery Connection SA permissions for Document AI, Vertex AI, and GCS
resource "google_project_iam_member" "bq_conn_docai_editor" {
  project = var.project_id
  role    = "roles/documentai.editor"
  member  = "serviceAccount:${google_bigquery_connection.vertex_docai_conn.cloud_resource[0].service_account_id}"
}

resource "google_project_iam_member" "bq_conn_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_bigquery_connection.vertex_docai_conn.cloud_resource[0].service_account_id}"
}

resource "google_storage_bucket_iam_member" "bq_conn_patient_docs_viewer" {
  bucket = google_storage_bucket.patient_docs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_bigquery_connection.vertex_docai_conn.cloud_resource[0].service_account_id}"
}

resource "google_storage_bucket_iam_member" "bq_conn_private_pdfs_viewer" {
  bucket = google_storage_bucket.private_pdfs.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_bigquery_connection.vertex_docai_conn.cloud_resource[0].service_account_id}"
}

# 6. Dedicated Service Account for the Clinical Intelligence App & Sync Jobs
resource "google_service_account" "app_sa" {
  account_id   = "clinical-intel-runner"
  display_name = "Clinical Case Intelligence Runner SA"
  depends_on   = [google_project_service.apis]
}

locals {
  app_sa_roles = [
    "roles/bigquery.jobUser",
    "roles/bigquery.dataEditor",
    "roles/bigquery.connectionUser",
    "roles/aiplatform.user",
    "roles/documentai.editor",
    "roles/storage.objectAdmin",
  ]
}

resource "google_project_iam_member" "app_sa_iam" {
  for_each = toset(local.app_sa_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.app_sa.email}"
}

# 7. Cloud Run Job for Incremental ClinicalTrials.gov v2 Ingestion (deployed once image is built)
resource "google_cloud_run_v2_job" "clinical_trials_sync" {
  count    = var.trials_sync_image != "" ? 1 : 0
  name     = "clinical-trials-incremental-sync"
  location = var.region

  template {
    template {
      service_account = google_service_account.app_sa.email
      timeout         = "1800s"
      containers {
        image = var.trials_sync_image
        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "BQ_DATASET_ID"
          value = var.dataset_id
        }
        env {
          name  = "LOOKBACK_DAYS"
          value = "30"
        }
        resources {
          limits = {
            cpu    = "2"
            memory = "4Gi"
          }
        }
      }
    }
  }
  depends_on = [google_project_service.apis, google_project_iam_member.app_sa_iam]
}

# 8. Cloud Run Service for the Clinician/Researcher Web UI & ADK Agent Pipeline
resource "google_cloud_run_v2_service" "clinical_app" {
  count    = var.app_image != "" ? 1 : 0
  name     = "clinical-case-intelligence"
  location = var.region

  template {
    service_account = google_service_account.app_sa.email
    timeout         = "600s"
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    containers {
      image = var.app_image
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "BQ_LOCATION"
        value = var.bq_location
      }
      env {
        name  = "BQ_DATASET_ID"
        value = var.dataset_id
      }
      env {
        name  = "BQ_CONNECTION_ID"
        value = "${var.project_id}.${var.bq_location}.${var.bq_connection_id}"
      }
      env {
        name  = "PATIENT_DOCS_BUCKET"
        value = google_storage_bucket.patient_docs.name
      }
      env {
        name  = "PRIVATE_PDFS_BUCKET"
        value = google_storage_bucket.private_pdfs.name
      }
      resources {
        limits = {
          cpu    = "2"
          memory = "4Gi"
        }
      }
    }
  }
  depends_on = [google_project_service.apis, google_project_iam_member.app_sa_iam]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count    = var.app_image != "" && var.enable_public_access ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.clinical_app[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
