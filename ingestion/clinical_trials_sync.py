"""Incremental ClinicalTrials.gov v2 API -> BigQuery Sync Job.

Fetches updated/recruiting studies from https://clinicaltrials.gov/api/v2/studies,
loads into `clinical_trials_staging`, and executes an idempotent MERGE into
`clinical_trials` (where BigQuery Autonomous AI.EMBED asynchronously computes
embeddings for newly inserted or updated rows).
"""

from __future__ import annotations

import datetime
import logging
import os
import time
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CT_API_URL = "https://clinicaltrials.gov/api/v2/studies"


def _join_list(values: list[str] | None, sep: str = "; ") -> str:
  if not values:
    return ""
  return sep.join(str(v).strip() for v in values if v)


def parse_study(study: dict[str, Any]) -> dict[str, Any] | None:
  """Flattens a ClinicalTrials.gov v2 JSON study into the BigQuery schema."""
  protocol = study.get("protocolSection", {})
  ident = protocol.get("identificationModule", {})
  status = protocol.get("statusModule", {})
  design = protocol.get("designModule", {})
  sponsor = protocol.get("sponsorCollaboratorsModule", {})
  conditions = protocol.get("conditionsModule", {})
  arms = protocol.get("armsInterventionsModule", {})
  desc = protocol.get("descriptionModule", {})
  elig = protocol.get("eligibilityModule", {})
  contacts = protocol.get("contactsLocationsModule", {})

  nct_id = ident.get("nctId")
  if not nct_id:
    return None

  brief_title = ident.get("briefTitle", "")
  official_title = ident.get("officialTitle", "")
  overall_status = status.get("overallStatus", "UNKNOWN")
  phases = design.get("phases", [])
  phase = _join_list(phases, ", ") or "NA"
  study_type = design.get("studyType", "UNKNOWN")
  lead_sponsor = sponsor.get("leadSponsor", {}).get("name", "")
  conditions_text = _join_list(conditions.get("conditions", []))

  interventions = [
      f"{i.get('type', '')}: {i.get('name', '')}".strip(": ")
      for i in arms.get("interventions", [])
      if i.get("name")
  ]
  interventions_text = _join_list(interventions)
  brief_summary = desc.get("briefSummary", "")
  eligibility_criteria = elig.get("eligibilityCriteria", "")
  min_age = elig.get("minimumAge", "")
  max_age = elig.get("maximumAge", "")
  sex = elig.get("sex", "ALL")

  locations = contacts.get("locations", [])
  countries = sorted({loc.get("country", "") for loc in locations if loc.get("country")})
  locations_summary = (
      f"{len(locations)} site(s): {', '.join(countries[:8])}"
      if locations
      else "Not specified"
  )

  raw_date = (
      status.get("lastUpdatePostDateStruct", {}).get("date")
      or status.get("lastUpdateSubmitDate")
      or datetime.date.today().isoformat()
  )
  # Normalize YYYY-MM -> YYYY-MM-01 for BigQuery DATE compatibility
  if len(raw_date) == 7:
    raw_date = f"{raw_date}-01"
  elif len(raw_date) == 4:
    raw_date = f"{raw_date}-01-01"

  # Build rich searchable text for autonomous AI.EMBED
  searchable_text = (
      f"Title: {brief_title}\n"
      f"Status: {overall_status} | Phase: {phase}\n"
      f"Conditions: {conditions_text}\n"
      f"Interventions: {interventions_text}\n"
      f"Summary: {brief_summary[:1500]}\n"
      f"Eligibility: {eligibility_criteria[:2000]}"
  )

  return {
      "nct_id": nct_id,
      "brief_title": brief_title,
      "official_title": official_title,
      "overall_status": overall_status,
      "phase": phase,
      "study_type": study_type,
      "lead_sponsor": lead_sponsor,
      "conditions_text": conditions_text,
      "interventions_text": interventions_text,
      "brief_summary": brief_summary,
      "eligibility_criteria": eligibility_criteria,
      "min_age": min_age,
      "max_age": max_age,
      "sex": sex,
      "locations_summary": locations_summary,
      "study_url": f"https://clinicaltrials.gov/study/{nct_id}",
      "last_update_date": raw_date,
      "searchable_text": searchable_text,
  }


def fetch_updated_studies(
    lookback_days: int = 30,
    max_studies: int = 10000,
    condition_filter: str | None = None,
) -> list[dict[str, Any]]:
  """Pulls paginated studies from ClinicalTrials.gov v2 API."""
  import requests  # noqa: PLC0415

  since_date = (
      datetime.date.today() - datetime.timedelta(days=lookback_days)
  ).isoformat()
  params: dict[str, Any] = {
      "pageSize": 1000,
      "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{since_date},MAX]",
      "filter.overallStatus": "RECRUITING,NOT_YET_RECRUITING,ENROLLING_BY_INVITATION,ACTIVE_NOT_RECRUITING",
  }
  if condition_filter:
    params["query.cond"] = condition_filter

  rows: list[dict[str, Any]] = []
  page_token: str | None = None

  while len(rows) < max_studies:
    if page_token:
      params["pageToken"] = page_token
    resp = requests.get(CT_API_URL, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    studies = payload.get("studies", [])
    if not studies:
      break
    for s in studies:
      parsed = parse_study(s)
      if parsed:
        rows.append(parsed)
    logger.info("Fetched %d studies so far...", len(rows))
    page_token = payload.get("nextPageToken")
    if not page_token:
      break
    time.sleep(0.25)

  return rows[:max_studies]


def load_and_merge(
    project_id: str,
    dataset_id: str,
    rows: list[dict[str, Any]],
) -> None:
  """Loads rows into staging and MERGEs into clinical_trials (triggers Autonomous AI.EMBED)."""
  if not rows:
    logger.info("No new/updated clinical trials found; skipping BigQuery MERGE.")
    return

  from google.cloud import bigquery  # noqa: PLC0415

  client = bigquery.Client(project=project_id)
  staging_table = f"{project_id}.{dataset_id}.clinical_trials_staging"
  target_table = f"{project_id}.{dataset_id}.clinical_trials"

  job_config = bigquery.LoadJobConfig(
      write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
      source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
  )
  logger.info("Loading %d trials into staging table %s...", len(rows), staging_table)
  load_job = client.load_table_from_json(rows, staging_table, job_config=job_config)
  load_job.result()

  merge_sql = f"""
  MERGE `{target_table}` T
  USING `{staging_table}` S
  ON T.nct_id = S.nct_id
  WHEN MATCHED AND (
    T.last_update_date < S.last_update_date
    OR T.overall_status != S.overall_status
  ) THEN
    UPDATE SET
      brief_title = S.brief_title,
      official_title = S.official_title,
      overall_status = S.overall_status,
      phase = S.phase,
      study_type = S.study_type,
      lead_sponsor = S.lead_sponsor,
      conditions_text = S.conditions_text,
      interventions_text = S.interventions_text,
      brief_summary = S.brief_summary,
      eligibility_criteria = S.eligibility_criteria,
      min_age = S.min_age,
      max_age = S.max_age,
      sex = S.sex,
      locations_summary = S.locations_summary,
      study_url = S.study_url,
      last_update_date = S.last_update_date,
      searchable_text = S.searchable_text,
      synced_at = CURRENT_TIMESTAMP()
  WHEN NOT MATCHED THEN
    INSERT (
      nct_id, brief_title, official_title, overall_status, phase, study_type,
      lead_sponsor, conditions_text, interventions_text, brief_summary,
      eligibility_criteria, min_age, max_age, sex, locations_summary,
      study_url, last_update_date, searchable_text, synced_at
    )
    VALUES (
      S.nct_id, S.brief_title, S.official_title, S.overall_status, S.phase, S.study_type,
      S.lead_sponsor, S.conditions_text, S.interventions_text, S.brief_summary,
      S.eligibility_criteria, S.min_age, S.max_age, S.sex, S.locations_summary,
      S.study_url, S.last_update_date, S.searchable_text, CURRENT_TIMESTAMP()
    );
  """
  logger.info("Executing MERGE into %s...", target_table)
  merge_job = client.query(merge_sql)
  merge_job.result()
  logger.info("ClinicalTrials.gov sync complete! Autonomous AI.EMBED will populate embeddings asynchronously.")


def main() -> None:
  project_id = os.environ["GCP_PROJECT_ID"]
  dataset_id = os.environ.get("BQ_DATASET_ID", "clinical_intelligence")
  lookback_days = int(os.environ.get("LOOKBACK_DAYS", "30"))
  max_studies = int(os.environ.get("MAX_STUDIES", "10000"))
  condition_filter = os.environ.get("CONDITION_FILTER") or None

  rows = fetch_updated_studies(
      lookback_days=lookback_days,
      max_studies=max_studies,
      condition_filter=condition_filter,
  )
  load_and_merge(project_id=project_id, dataset_id=dataset_id, rows=rows)


if __name__ == "__main__":
  main()
