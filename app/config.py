"""Runtime configuration for the Clinical Case Intelligence application."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
  gcp_project_id: str = os.environ.get("GCP_PROJECT_ID", "")
  gcp_region: str = os.environ.get("GCP_REGION", "us-central1")
  bq_location: str = os.environ.get("BQ_LOCATION", "US")
  bq_dataset_id: str = os.environ.get("BQ_DATASET_ID", "clinical_intelligence")
  bq_connection_id: str = os.environ.get(
      "BQ_CONNECTION_ID",
      f"{os.environ.get('GCP_PROJECT_ID', '')}.{os.environ.get('BQ_LOCATION', 'US')}.vertex_docai_conn",
  )
  patient_docs_bucket: str = os.environ.get("PATIENT_DOCS_BUCKET", "")
  private_pdfs_bucket: str = os.environ.get("PRIVATE_PDFS_BUCKET", "")
  gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
  gemini_fast_model: str = os.environ.get("GEMINI_FAST_MODEL", "gemini-2.5-flash")
  demo_mode: bool = os.environ.get("DEMO_MODE", "false").lower() == "true"


settings = Settings()
