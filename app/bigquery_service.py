"""BigQuery AI & GCS Service Layer.

Handles:
1. Uploading patient case PDFs/DOCX/PPTX to GCS.
2. Invoking BigQuery AI.PARSE_DOCUMENT (layout-v1 + raw-docai) & AI.CHUNK_DOCUMENT.
3. Executing zero-full-scan two-stage hybrid retrieval on PubMed, ClinicalTrials,
   and optional customer Private PDFs.
4. Recording exact BigQuery bytes_processed / bytes_billed telemetry for every step.
"""

from __future__ import annotations

import logging
from typing import Any

from config import settings
from google.cloud import bigquery
from google.cloud import storage

logger = logging.getLogger(__name__)


class BigQueryClinicalService:
  """Encapsulates all BigQuery AI Document Intelligence and Hybrid Search calls."""

  def __init__(self) -> None:
    self.project_id = settings.gcp_project_id
    self.dataset_id = settings.bq_dataset_id
    self.connection_id = settings.bq_connection_id
    self.patient_bucket = settings.patient_docs_bucket
    self._bq: bigquery.Client | None = None
    self._gcs: storage.Client | None = None

  @property
  def bq(self) -> bigquery.Client:
    if self._bq is None:
      self._bq = bigquery.Client(project=self.project_id, location=settings.bq_location)
    return self._bq

  @property
  def gcs(self) -> storage.Client:
    if self._gcs is None:
      self._gcs = storage.Client(project=self.project_id)
    return self._gcs

  def _extract_telemetry(self, job: bigquery.QueryJob, label: str) -> dict[str, Any]:
    bytes_processed = job.total_bytes_processed or 0
    bytes_billed = job.total_bytes_billed or 0
    return {
        "step": label,
        "job_id": job.job_id,
        "bytes_processed": bytes_processed,
        "mb_processed": round(bytes_processed / (1024 * 1024), 2),
        "bytes_billed": bytes_billed,
        "cache_hit": bool(job.cache_hit),
        "slot_ms": job.slot_millis or 0,
    }

  def upload_patient_document(
      self, case_id: str, filename: str, content: bytes, content_type: str
  ) -> str:
    """Uploads a doctor-submitted file (PDF/DOCX/PPTX) to GCS and returns gs:// URI."""
    bucket = self.gcs.bucket(self.patient_bucket)
    blob_path = f"cases/{case_id}/{filename}"
    blob = bucket.blob(blob_path)
    blob.upload_from_string(content, content_type=content_type)
    uri = f"gs://{self.patient_bucket}/{blob_path}"
    logger.info("Uploaded patient document to %s", uri)
    return uri

  def parse_and_chunk_patient_document(
      self, case_id: str, gcs_uri: str
  ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Runs BigQuery AI.PARSE_DOCUMENT + AI.CHUNK_DOCUMENT via stored procedure."""
    telemetry: list[dict[str, Any]] = []

    proc_sql = f"""
    CALL `{self.project_id}.{self.dataset_id}.sp_ingest_patient_document`(@case_id, @gcs_uri);
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("case_id", "STRING", case_id),
            bigquery.ScalarQueryParameter("gcs_uri", "STRING", gcs_uri),
        ]
    )
    job = self.bq.query(proc_sql, job_config=job_config)
    job.result()
    telemetry.append(self._extract_telemetry(job, f"AI.PARSE_DOCUMENT + AI.CHUNK_DOCUMENT ({gcs_uri.split('/')[-1]})"))

    # Fetch parsed HTML tables & clean text + chunks for this document (uncorrelated)
    fetch_sql = f"""
    SELECT
      p.gcs_uri,
      p.extracted_tables,
      p.clean_text_without_boilerplate,
      ARRAY(
        SELECT AS STRUCT chunk_id, start_page, end_page, content
        FROM `{self.project_id}.{self.dataset_id}.patient_case_chunks` c
        WHERE c.case_id = @case_id AND c.gcs_uri = @gcs_uri
        ORDER BY chunk_id
      ) AS chunks
    FROM `{self.project_id}.{self.dataset_id}.patient_case_parsed_docs` p
    WHERE p.case_id = @case_id AND p.gcs_uri = @gcs_uri
    ORDER BY p.parsed_at DESC
    LIMIT 1
    """
    fetch_job = self.bq.query(fetch_sql, job_config=job_config)
    rows = [dict(r) for r in fetch_job.result()]
    telemetry.append(self._extract_telemetry(fetch_job, "Fetch Parsed Patient Document"))

    parsed = rows[0] if rows else {
        "gcs_uri": gcs_uri,
        "extracted_tables": [],
        "clean_text_without_boilerplate": "",
        "chunks": [],
    }
    return parsed, telemetry

  def search_pubmed_hybrid(
      self,
      semantic_query: str,
      lexical_biomarkers: str,
      min_pub_year: int = 2018,
      top_k: int = 12,
  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Executes two-stage hybrid PubMed search (TREE_AH vector + Search Index -> Top-K hydration)."""
    sql = f"""
    CALL `{self.project_id}.{self.dataset_id}.sp_search_pubmed_hybrid`(
      @semantic_query,
      @lexical_biomarkers,
      @min_pub_year,
      @top_k
    );
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("semantic_query", "STRING", semantic_query),
            bigquery.ScalarQueryParameter("lexical_biomarkers", "STRING", lexical_biomarkers),
            bigquery.ScalarQueryParameter("min_pub_year", "INT64", min_pub_year),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ]
    )
    job = self.bq.query(sql, job_config=job_config)
    rows = [dict(r) for r in job.result()]
    return rows, self._extract_telemetry(job, "PubMed Hybrid Search + Top-K Hydration")

  def search_clinical_trials_hybrid(
      self,
      semantic_query: str,
      lexical_biomarkers: str,
      recruiting_only: bool = True,
      top_k: int = 10,
  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Executes cluster-pruned hybrid AI.SEARCH over ClinicalTrials.gov."""
    sql = f"""
    CALL `{self.project_id}.{self.dataset_id}.sp_search_clinical_trials_hybrid`(
      @semantic_query,
      @lexical_biomarkers,
      @recruiting_only,
      @top_k
    );
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("semantic_query", "STRING", semantic_query),
            bigquery.ScalarQueryParameter("lexical_biomarkers", "STRING", lexical_biomarkers),
            bigquery.ScalarQueryParameter("recruiting_only", "BOOL", recruiting_only),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ]
    )
    job = self.bq.query(sql, job_config=job_config)
    rows = [dict(r) for r in job.result()]
    return rows, self._extract_telemetry(job, "ClinicalTrials Hybrid AI.SEARCH")

  def search_private_pdfs(
      self, semantic_query: str, top_k: int = 5
  ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Queries optional customer private PDFs ingested via AI.CHUNK_DOCUMENT + AI.EMBED."""
    sql = f"""
    SELECT
      base.uri,
      base.chunk_id,
      base.start_page,
      base.end_page,
      base.content,
      distance
    FROM AI.SEARCH(
      TABLE `{self.project_id}.{self.dataset_id}.private_medical_chunks`,
      'content',
      @semantic_query,
      top_k => 10
    )
    ORDER BY distance ASC
    LIMIT @top_k;
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("semantic_query", "STRING", semantic_query),
            bigquery.ScalarQueryParameter("top_k", "INT64", top_k),
        ]
    )
    job = self.bq.query(sql, job_config=job_config)
    rows = [dict(r) for r in job.result()]
    return rows, self._extract_telemetry(job, "Private Institutional PDFs AI.SEARCH")
