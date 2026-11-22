"""Unit tests for Benchmarking Engine, ClinicalTrials Parser, and FastAPI endpoints."""

from __future__ import annotations

import pathlib
import sys
import unittest

APP_DIR = pathlib.Path(__file__).resolve().parents[1]
INGESTION_DIR = APP_DIR.parent / "ingestion"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(INGESTION_DIR))

from benchmarking import benchmark_clinical_trial, benchmark_pubmed_article  # noqa: E402
from clinical_trials_sync import parse_study  # noqa: E402


class TestBenchmarkingEngine(unittest.TestCase):
  def test_pubmed_benchmark_prioritizes_high_evidence_and_biomarker_match(self) -> None:
    biomarkers = ["FLT3-ITD", "KMT2A-AF6", "CD33"]
    strong_trial = benchmark_pubmed_article(
        {
            "pmc_id": "PMC1",
            "title": "Phase 2 trial of menin inhibition in FLT3-ITD and KMT2A-AF6 CD33+ AML",
            "study_type_inferred": "CLINICAL_TRIAL",
            "sjr_score": 9.5,
            "sjr_quartile": "Q1",
            "pub_year": 2025,
            "cosine_distance": 0.12,
            "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
        },
        patient_biomarkers=biomarkers,
        current_year=2026,
    )
    weak_case = benchmark_pubmed_article(
        {
            "pmc_id": "PMC2",
            "title": "Case report of adult solid tumor",
            "study_type_inferred": "CASE_REPORT",
            "sjr_score": 0.5,
            "sjr_quartile": "Q3",
            "pub_year": 2016,
            "cosine_distance": 0.45,
            "retrieval_mode": "SEMANTIC_VECTOR_MATCH",
        },
        patient_biomarkers=biomarkers,
        current_year=2026,
    )
    self.assertGreater(
        strong_trial["benchmark"]["total_score"],
        weak_case["benchmark"]["total_score"],
    )
    self.assertEqual(
        set(strong_trial["benchmark"]["matched_biomarkers"]),
        {"FLT3-ITD", "KMT2A-AF6", "CD33"},
    )
    self.assertGreater(strong_trial["benchmark"]["total_score"], 80.0)

  def test_clinical_trial_age_and_sex_eligibility_checks(self) -> None:
    trial_data = {
        "nct_id": "NCT05761171",
        "brief_title": "Revumenib in KMT2A-AF6 Pediatric AML",
        "overall_status": "RECRUITING",
        "phase": "PHASE2",
        "min_age": "1 Month",
        "max_age": "21 Years",
        "sex": "ALL",
        "eligibility_criteria": "KMT2A-AF6 positive AML",
        "cosine_distance": 0.15,
        "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
    }
    eligible = benchmark_clinical_trial(
        trial_data,
        patient_biomarkers=["KMT2A-AF6"],
        patient_age_years=15,
        patient_sex="MALE",
    )
    ineligible_age = benchmark_clinical_trial(
        trial_data,
        patient_biomarkers=["KMT2A-AF6"],
        patient_age_years=45,
        patient_sex="MALE",
    )
    self.assertGreater(
        eligible["benchmark"]["total_score"],
        ineligible_age["benchmark"]["total_score"],
    )
    statuses = {c["criterion"]: c["status"] for c in ineligible_age["benchmark"]["eligibility_checklist"]}
    self.assertTrue(any(v == "FAIL" for v in statuses.values()))


class TestClinicalTrialsParser(unittest.TestCase):
  def test_parse_study_flattens_v2_payload(self) -> None:
    sample_v2 = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT09999999",
                "briefTitle": "Selinexor + Venetoclax Pediatric AML",
            },
            "statusModule": {
                "overallStatus": "RECRUITING",
                "lastUpdatePostDateStruct": {"date": "2026-08"},
            },
            "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE1", "PHASE2"]},
            "conditionsModule": {"conditions": ["Acute Myeloid Leukemia"]},
            "armsInterventionsModule": {
                "interventions": [{"type": "DRUG", "name": "Selinexor"}]
            },
            "eligibilityModule": {
                "eligibilityCriteria": "Inclusion: KMT2A-r or FLT3-ITD",
                "minimumAge": "1 Year",
                "maximumAge": "21 Years",
                "sex": "ALL",
            },
            "contactsLocationsModule": {
                "locations": [{"country": "Netherlands"}, {"country": "United States"}]
            },
        }
    }
    row = parse_study(sample_v2)
    self.assertIsNotNone(row)
    assert row is not None
    self.assertEqual(row["nct_id"], "NCT09999999")
    self.assertEqual(row["last_update_date"], "2026-08-01")
    self.assertIn("Selinexor", row["interventions_text"])
    self.assertIn("Netherlands", row["locations_summary"])


if __name__ == "__main__":
  unittest.main()
