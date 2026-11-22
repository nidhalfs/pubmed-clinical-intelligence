"""FastAPI Web Server for Clinical Case Intelligence (Cloud Run)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agents.pipeline import ClinicalCaseOrchestrator
from benchmarking import benchmark_clinical_trial, benchmark_pubmed_article
from config import settings
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="PubMed & ClinicalTrials Case Intelligence",
    description="Optimized BigQuery AI Document Intelligence + Zero-Full-Scan Hybrid Search for Clinicians & Researchers",
    version="1.0.0",
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

orchestrator = ClinicalCaseOrchestrator()


@app.get("/")
def serve_ui() -> FileResponse:
  return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health() -> dict[str, Any]:
  return {
      "status": "ok",
      "project_id": settings.gcp_project_id or "not-configured",
      "dataset_id": settings.bq_dataset_id,
      "demo_mode": settings.demo_mode,
  }


@app.post("/api/analyze")
async def analyze_case(
    clinical_notes: str = Form(...),
    min_pub_year: int = Form(2018),
    recruiting_only: bool = Form(True),
    include_private_pdfs: bool = Form(False),
    top_k_pubmed: int = Form(10),
    top_k_trials: int = Form(8),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
  """Uploads patient PDFs/DOCX, parses via BigQuery AI.PARSE_DOCUMENT/AI.CHUNK_DOCUMENT,
  runs zero-full-scan hybrid search on PubMed & ClinicalTrials, and returns benchmarked report."""
  if not settings.gcp_project_id:
    raise HTTPException(
        status_code=400,
        detail="GCP_PROJECT_ID is not configured. Deploy to GCP or use /api/demo to preview the workflow.",
    )

  uploaded_payloads: list[tuple[str, bytes, str]] = []
  for f in files:
    if not f.filename:
      continue
    content = await f.read()
    if content:
      uploaded_payloads.append(
          (f.filename, content, f.content_type or "application/pdf")
      )

  try:
    result = orchestrator.run(
        clinical_notes=clinical_notes,
        uploaded_files=uploaded_payloads,
        min_pub_year=min_pub_year,
        recruiting_only=recruiting_only,
        include_private_pdfs=include_private_pdfs,
        top_k_pubmed=top_k_pubmed,
        top_k_trials=top_k_trials,
    )
    return result
  except Exception as exc:  # noqa: BLE001
    logger.exception("Case analysis failed")
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/demo")
def run_demo_case() -> dict[str, Any]:
  """Returns a realistic pediatric AML tumor-board benchmarked demonstration
  (runs offline without requiring live GCP credentials)."""
  biomarkers = ["FLT3-ITD", "KMT2A-AF6", "CD33", "CD38", "CD123"]
  sample_pubmed = [
      benchmark_pubmed_article(
          {
              "pmc_id": "PMC10842190",
              "pmid": "38291042",
              "title": "Menin Inhibition Combined with Venetoclax and FLT3 Inhibitors in Relapsed KMT2A-Rearranged and FLT3-ITD Pediatric AML",
              "author": "Ilan U, Zwaan CM, et al.",
              "journal_name": "Blood",
              "pub_year": 2025,
              "study_type_inferred": "CLINICAL_TRIAL",
              "pmc_link": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10842190/",
              "cosine_distance": 0.14,
              "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
              "sjr_score": 6.8,
              "sjr_quartile": "Q1",
              "hydrated_text_excerpt": "Phase 1/2 multicenter study evaluating revumenib (menin inhibitor) combined with venetoclax and gilteritinib in pediatric/AYA patients with relapsed KMT2A-AF6 and FLT3-ITD AML showing synergistic blast clearance.",
          },
          patient_biomarkers=biomarkers,
      ),
      benchmark_pubmed_article(
          {
              "pmc_id": "PMC10411822",
              "pmid": "37651209",
              "title": "Selinexor Plus Venetoclax in Multiply Relapsed Pediatric Acute Myeloid Leukemia: Preclinical Synergy and Clinical Activity",
              "author": "Bourquin JP, et al.",
              "journal_name": "Leukemia",
              "pub_year": 2024,
              "study_type_inferred": "COHORT_STUDY",
              "pmc_link": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10411822/",
              "cosine_distance": 0.19,
              "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
              "sjr_score": 4.6,
              "sjr_quartile": "Q1",
              "hydrated_text_excerpt": "Drug response profiling (DRP) identified sensitivity to XPO1 inhibition (selinexor) combined with BCL-2 inhibition (venetoclax) in KMT2A-rearranged and CD33+/CD38+ refractory AML.",
          },
          patient_biomarkers=biomarkers,
      ),
      benchmark_pubmed_article(
          {
              "pmc_id": "PMC9984311",
              "pmid": "36912874",
              "title": "CD123- and CD33-Targeted Immunotherapies in Relapsed/Refractory Pediatric AML After Allogeneic HSCT",
              "author": "Tasian SK, et al.",
              "journal_name": "J Clin Oncol",
              "pub_year": 2024,
              "study_type_inferred": "META_ANALYSIS",
              "pmc_link": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9984311/",
              "cosine_distance": 0.23,
              "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
              "sjr_score": 9.4,
              "sjr_quartile": "Q1",
              "hydrated_text_excerpt": "Systematic review of CD123-directed (tagraxofusp, pivekimab sunirine) and CD33-directed therapies in post-HSCT relapsed pediatric AML.",
          },
          patient_biomarkers=biomarkers,
      ),
  ]

  sample_trials = [
      benchmark_clinical_trial(
          {
              "nct_id": "NCT05761171",
              "brief_title": "Revumenib (SNDX-5613) in Combination with Chemotherapy / Venetoclax for Relapsed or Refractory KMT2A-Rearranged Pediatric AML",
              "overall_status": "RECRUITING",
              "phase": "PHASE2",
              "study_type": "INTERVENTIONAL",
              "lead_sponsor": "Children's Oncology Group / Syndax",
              "conditions_text": "Relapsed/Refractory Acute Myeloid Leukemia; KMT2A-rearranged AML",
              "interventions_text": "DRUG: Revumenib; DRUG: Venetoclax",
              "min_age": "1 Month",
              "max_age": "21 Years",
              "sex": "ALL",
              "locations_summary": "28 site(s): United States, Netherlands, Germany, France, Switzerland",
              "study_url": "https://clinicaltrials.gov/study/NCT05761171",
              "cosine_distance": 0.12,
              "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
              "eligibility_criteria": "Inclusion: Age 1 month to 21 years; Confirmed KMT2A rearrangement (including t(6;10) KMT2A-AF6); >=5% bone marrow blasts; Prior HSCT permitted if >60 days post-transplant without active GVHD.",
          },
          patient_biomarkers=biomarkers,
          patient_age_years=15,
          patient_sex="MALE",
      ),
      benchmark_clinical_trial(
          {
              "nct_id": "NCT04898894",
              "brief_title": "Selinexor and Venetoclax with or without Chemotherapy in Pediatric Patients with Relapsed/Refractory AML",
              "overall_status": "RECRUITING",
              "phase": "PHASE1, PHASE2",
              "study_type": "INTERVENTIONAL",
              "lead_sponsor": "St. Jude Children's Research Hospital",
              "conditions_text": "Pediatric Relapsed Acute Myeloid Leukemia",
              "interventions_text": "DRUG: Selinexor; DRUG: Venetoclax",
              "min_age": "1 Year",
              "max_age": "24 Years",
              "sex": "ALL",
              "locations_summary": "12 site(s): United States, Switzerland, Netherlands",
              "study_url": "https://clinicaltrials.gov/study/NCT04898894",
              "cosine_distance": 0.17,
              "retrieval_mode": "HYBRID_SEMANTIC_AND_EXACT",
              "eligibility_criteria": "Inclusion: Age 1-24 years; Relapsed/refractory AML with FLT3-ITD or KMT2A-r; CD33/CD38 positive blasts allowed.",
          },
          patient_biomarkers=biomarkers,
          patient_age_years=15,
          patient_sex="MALE",
      ),
  ]

  sample_pubmed.sort(key=lambda x: x["benchmark"]["total_score"], reverse=True)
  sample_trials.sort(key=lambda x: x["benchmark"]["total_score"], reverse=True)

  return {
      "case_id": "demo-aml-pediatric-001",
      "patient_profile": {
          "patient_summary": "15-year-old male with 2nd relapse M5 AML harboring dual high-risk drivers (FLT3-ITD and t(6;10) KMT2A-AF6 fusion) post-MUD HSCT and venetoclax/azacytidine, currently 50% bone marrow blasts with CD33+, CD38+, CD123+ immunophenotype and ex-vivo DRP sensitivity to Selinexor + Venetoclax.",
          "age_years": 15,
          "sex": "MALE",
          "primary_diagnosis": "Relapsed/Refractory M5 Acute Myeloid Leukemia (2nd Relapse)",
          "actionable_biomarkers": biomarkers,
          "prior_therapies": [
              "NOPHO DBH AML 2012",
              "Quizartinib",
              "FLA-Mylotarg",
              "MUD HSCT",
              "Venetoclax + Azacytidine (2 courses)",
          ],
          "key_lab_and_diagnostic_findings": [
              {"finding": "Bone marrow 50% blasts; peripheral blood 4% blasts", "source_citation": "Pathology_Report.pdf [Page 1]"},
              {"finding": "Cytogenetics: t(6;10) KMT2A-AF6 fusion; NGS: FLT3-ITD positive", "source_citation": "NGS_Panel_Relapse2.pdf [Page 2, Table 1]"},
              {"finding": "Immunophenotyping: CD33+, CD38+, CD123+ (1st relapse)", "source_citation": "Flow_Cytometry.pdf [Page 1, Table 2]"},
              {"finding": "Zurich DRP: ex-vivo sensitivity to Selinexor + Venetoclax", "source_citation": "DRP_Summary.pdf [Page 3]"},
          ],
          "exclusion_considerations": ["Prior allogeneic MUD HSCT", "Prior Quizartinib progression", "Prior Venetoclax exposure"],
      },
      "parsed_documents_count": 3,
      "benchmarked_pubmed": sample_pubmed,
      "benchmarked_trials": sample_trials,
      "private_pdf_matches": [],
      "markdown_report": (
          "## Executive Clinical Synthesis\n"
          "*(Research & educational decision-support only — not autonomous medical advice.)*\n\n"
          "This patient has **dual actionable molecular drivers (`KMT2A-AF6` + `FLT3-ITD`)** alongside surface targets (`CD33`, `CD38`, `CD123`) and ex-vivo **Selinexor + Venetoclax** sensitivity. "
          "Two high-priority multi-target strategies emerge from our benchmarked PubMed and ClinicalTrials.gov evidence:\n"
          "1. **Menin Inhibitor Combination (`Revumenib` + `Venetoclax` ± `FLT3` inhibitor):** Directly targets `KMT2A-AF6` while synergizing against `FLT3-ITD` clones (PMID: 38291042; Recruiting Phase 2 Trial NCT05761171).\n"
          "2. **DRP-Concordant `Selinexor + Venetoclax` Regimen:** Directly validated by the patient's Zurich DRP assay and supported by pediatric cohort outcomes (PMID: 37651209; Recruiting Phase 1/2 Trial NCT04898894).\n"
          "3. **CD123 / CD33-Directed Immunotherapy:** Viable bridge/salvage option if bone marrow blast immunophenotyping confirms persistent CD123/CD33 expression (PMID: 36912874)."
      ),
      "efficiency_telemetry": {
          "total_mb_processed": 42.6,
          "steps": [
              {"step": "AI.PARSE_DOCUMENT + AI.CHUNK_DOCUMENT (3 PDFs)", "mb_processed": 1.4, "cache_hit": False, "slot_ms": 1820},
              {"step": "PubMed Hybrid Search (TREE_AH + Search Index) + Top-10 Cold Hydration", "mb_processed": 31.8, "cache_hit": False, "slot_ms": 2410},
              {"step": "ClinicalTrials Hybrid AI.SEARCH (Cluster-Pruned Recruiting Only)", "mb_processed": 9.4, "cache_hit": False, "slot_ms": 1150},
          ],
      },
  }
