variable "project_id" {
  description = "Target customer GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run and Cloud Scheduler (must be in the same geography as bq_location, e.g. us-central1 for US or europe-west4 for EU)"
  type        = string
  default     = "us-central1"
}

variable "bq_location" {
  description = "BigQuery dataset location (US or EU; note AI.PARSE_DOCUMENT is GA in US/EU multi-regions and most US/EU single regions except europe-west2 and europe-west6)"
  type        = string
  default     = "US"
}

variable "dataset_id" {
  description = "BigQuery dataset ID for the optimized PubMed, ClinicalTrials, and Patient Case tables"
  type        = string
  default     = "clinical_intelligence"
}

variable "bq_connection_id" {
  description = "BigQuery Cloud Resource connection ID for Vertex AI and Document AI"
  type        = string
  default     = "vertex_docai_conn"
}

variable "app_image" {
  description = "Container image URI for the Clinical Case Intelligence Web App"
  type        = string
  default     = ""
}

variable "trials_sync_image" {
  description = "Container image URI for the ClinicalTrials.gov incremental sync Cloud Run Job"
  type        = string
  default     = ""
}

variable "enable_public_access" {
  description = "Whether to allow unauthenticated Cloud Run access (set false and use IAP/IAM for production clinical environments)"
  type        = bool
  default     = false
}
