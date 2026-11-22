output "dataset_id" {
  value = google_bigquery_dataset.clinical_intelligence.dataset_id
}

output "bq_connection_full_id" {
  value = "${var.project_id}.${var.bq_location}.${var.bq_connection_id}"
}

output "bq_connection_service_account" {
  value = google_bigquery_connection.vertex_docai_conn.cloud_resource[0].service_account_id
}

output "patient_docs_bucket" {
  value = google_storage_bucket.patient_docs.name
}

output "private_pdfs_bucket" {
  value = google_storage_bucket.private_pdfs.name
}

output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
}

output "app_service_account" {
  value = google_service_account.app_sa.email
}

output "app_url" {
  value = length(google_cloud_run_v2_service.clinical_app) > 0 ? google_cloud_run_v2_service.clinical_app[0].uri : "Not deployed yet (pass app_image)"
}
