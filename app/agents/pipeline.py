"""Google ADK Multi-Agent Pipeline & Programmatic Clinical Case Orchestrator.

Four specialized stages:
  1. CaseParserAgent: Structures doctor notes + AI.PARSE_DOCUMENT tables/chunks
     into actionable clinical events (diagnosis, biomarkers, labs, prior lines)
     with exact source page citations.
  2. RetrievalAgent: Runs zero-full-scan two-stage hybrid retrieval on optimized
     BigQuery PubMed, ClinicalTrials.gov, and optional Private PDF indexes.
  3. BenchmarkAnalystAgent: Applies deterministic multi-axis benchmarking
     (Biomarker Match, Evidence Hierarchy, Journal SJR, Trial Eligibility).
  4. ReportSynthesizerAgent: Produces an auditable clinician/researcher report
     with explanations, clickable PMID/PMC/NCT references, and efficiency stats.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from benchmarking import benchmark_clinical_trial, benchmark_pubmed_article
from bigquery_service import BigQueryClinicalService
from config import settings

logger = logging.getLogger(__name__)


CASE_EXTRACTION_PROMPT = """You are a senior clinical informaticist preparing a tumor board / complex case brief.
Analyze the doctor's clinical notes AND the parsed document excerpts (extracted via BigQuery AI.PARSE_DOCUMENT with [Page N] markers and HTML lab/genomic tables).

Return ONLY valid JSON with this exact schema:
{
  "patient_summary": "2-3 sentence clinical summary",
  "age_years": 15,
  "sex": "MALE | FEMALE | UNKNOWN",
  "primary_diagnosis": "Primary disease / subtype",
  "actionable_biomarkers": ["FLT3-ITD", "KMT2A-AF6", "CD33", "CD38", "CD123"],
  "prior_therapies": ["NOPHO DBH AML 2012", "Quizartinib", "FLA-Mylotarg", "MUD HSCT", "Venetoclax + Azacytidine"],
  "key_lab_and_diagnostic_findings": [
    {"finding": "Bone marrow 50% blasts", "source_citation": "Clinical Notes / Doc Page 1"}
  ],
  "exclusion_considerations": ["Prior allogeneic HSCT", "Relapsed/refractory"],
  "pubmed_semantic_query": "Rich conceptual biomedical query for vector search",
  "pubmed_lexical_biomarkers": "Space-separated exact gene/marker/drug terms for BigQuery Search Index",
  "clinical_trials_query": "Targeted query for recruiting interventional clinical trials"
}
"""

REPORT_SYNTHESIS_PROMPT = """You are a medical research evidence synthesizer assisting a physician or clinical researcher.
DISCLAIMER: This report is for research and educational decision-support only, not autonomous medical advice.

Given:
1. Structured Patient Profile (with document page citations)
2. Top Benchmarked PubMed Articles (with exact PMID, PMC ID, links, benchmark scores, and hydrated text)
3. Top Benchmarked Clinical Trials (with exact NCT ID, status, phase, eligibility checklist, and benchmark scores)
4. Optional Institutional Private PDF Chunks

Write a concise, rigorous, clinician-ready Markdown report with these exact sections:
1. **Executive Clinical Synthesis** — Best-fitting therapeutic hypotheses, highlighting any single drug or combination targeting MULTIPLE actionable events.
2. **Actionable Patient Profile & Document Grounding** — Bullet summary citing the exact source document pages (`[Page N]`).
3. **Top Benchmarked PubMed Literature & Relevant Case Studies** — Markdown table (`Rank | Benchmark Score | Study & Citation (PMID/PMC) | Study Type & Journal | Clinical Fit Explanation`) followed by detailed clinical takeaways for the top 3-5 papers.
4. **Top Benchmarked Clinical Trials** — Markdown table (`Rank | Benchmark Score | Trial (NCT ID) | Status & Phase | Eligibility Fit | Rationale`) followed by key inclusion/exclusion verification points.
5. **Benchmarking Methodology & Evidence Caveats** — Explain how candidates were scored (Biomarker Match, Evidence Level, Journal SJR, Recency, Eligibility) and note any missing patient data needed to confirm eligibility.

Cite ONLY the provided PMIDs, PMC IDs, and NCT IDs — never fabricate citations.
"""


def _safe_json_loads(raw_text: str) -> dict[str, Any]:
  """Extracts JSON object from a Gemini response (strips markdown fences if present)."""
  cleaned = raw_text.strip()
  cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
  cleaned = re.sub(r"\s*```$", "", cleaned)
  return json.loads(cleaned)


def _get_genai_client() -> Any:
  from google import genai
  return genai.Client(
      vertexai=True,
      project=settings.gcp_project_id,
      location=settings.vertex_location,
  )


def build_adk_root_agent() -> Any:
  """Builds the composable Google ADK SequentialAgent for `adk web` or Vertex AI Agent Engine."""
  from google.adk.agents import Agent, SequentialAgent

  bq_service = BigQueryClinicalService()

  def tool_search_pubmed(
      semantic_query: str,
      lexical_biomarkers: str,
      min_pub_year: int = 2018,
      top_k: int = 10,
  ) -> dict[str, Any]:
    """Runs two-stage hybrid PubMed search on the lean TREE_AH-indexed BigQuery table."""
    rows, telemetry = bq_service.search_pubmed_hybrid(
        semantic_query=semantic_query,
        lexical_biomarkers=lexical_biomarkers,
        min_pub_year=min_pub_year,
        top_k=top_k,
    )
    return {"articles": rows, "telemetry": telemetry}

  def tool_search_clinical_trials(
      semantic_query: str,
      lexical_biomarkers: str,
      recruiting_only: bool = True,
      top_k: int = 8,
  ) -> dict[str, Any]:
    """Runs cluster-pruned hybrid AI.SEARCH over ClinicalTrials.gov in BigQuery."""
    rows, telemetry = bq_service.search_clinical_trials_hybrid(
        semantic_query=semantic_query,
        lexical_biomarkers=lexical_biomarkers,
        recruiting_only=recruiting_only,
        top_k=top_k,
    )
    return {"trials": rows, "telemetry": telemetry}

  def tool_benchmark_evidence(
      articles: list[dict[str, Any]],
      trials: list[dict[str, Any]],
      biomarkers: list[str],
      age_years: float | None = None,
      sex: str | None = None,
  ) -> dict[str, Any]:
    """Computes transparent multi-axis benchmark scores for PubMed articles and Clinical Trials."""
    scored_articles = sorted(
        [benchmark_pubmed_article(a, biomarkers) for a in articles],
        key=lambda x: x["benchmark"]["total_score"],
        reverse=True,
    )
    scored_trials = sorted(
        [benchmark_clinical_trial(t, biomarkers, age_years, sex) for t in trials],
        key=lambda x: x["benchmark"]["total_score"],
        reverse=True,
    )
    return {"benchmarked_articles": scored_articles, "benchmarked_trials": scored_trials}

  case_parser_agent = Agent(
      name="case_parser_agent",
      model=settings.gemini_fast_model,
      description="Extracts structured clinical profile, biomarkers, and document page citations.",
      instruction=CASE_EXTRACTION_PROMPT,
      output_key="patient_profile",
  )

  retrieval_agent = Agent(
      name="retrieval_agent",
      model=settings.gemini_fast_model,
      description="Retrieves targeted PubMed papers and recruiting Clinical Trials without full-table scans.",
      instruction=(
          "Using {patient_profile}, call `tool_search_pubmed` and `tool_search_clinical_trials` "
          "to retrieve the most relevant articles and recruiting trials. Return both results and telemetry."
      ),
      tools=[tool_search_pubmed, tool_search_clinical_trials],
      output_key="retrieved_evidence",
  )

  benchmark_analyst_agent = Agent(
      name="benchmark_analyst_agent",
      model=settings.gemini_fast_model,
      description="Scores and benchmarks retrieved papers and trials transparently.",
      instruction=(
          "Call `tool_benchmark_evidence` on {retrieved_evidence} using the patient's biomarkers, "
          "age, and sex from {patient_profile}. Return the ranked benchmarked results."
      ),
      tools=[tool_benchmark_evidence],
      output_key="benchmarked_evidence",
  )

  report_synthesizer_agent = Agent(
      name="report_synthesizer_agent",
      model=settings.gemini_model,
      description="Synthesizes the final clinician/researcher report with citations and benchmark explanations.",
      instruction=REPORT_SYNTHESIS_PROMPT + "\n\nUse {patient_profile} and {benchmarked_evidence}.",
      output_key="final_report",
  )

  return SequentialAgent(
      name="clinical_case_intelligence_pipeline",
      sub_agents=[
          case_parser_agent,
          retrieval_agent,
          benchmark_analyst_agent,
          report_synthesizer_agent,
      ],
  )


class ClinicalCaseOrchestrator:
  """End-to-end orchestrator used by FastAPI for deterministic JSON + Markdown output."""

  def __init__(self, bq_service: BigQueryClinicalService | None = None) -> None:
    self.bq_service = bq_service or BigQueryClinicalService()

  def run(
      self,
      clinical_notes: str,
      uploaded_files: list[tuple[str, bytes, str]] | None = None,
      min_pub_year: int = 2018,
      recruiting_only: bool = True,
      include_private_pdfs: bool = False,
      top_k_pubmed: int = 10,
      top_k_trials: int = 8,
  ) -> dict[str, Any]:
    case_id = f"case-{uuid.uuid4().hex[:10]}"
    telemetry: list[dict[str, Any]] = []
    parsed_docs: list[dict[str, Any]] = []

    # 1. Ingest uploaded patient documents via GCS + BigQuery AI.PARSE_DOCUMENT & AI.CHUNK_DOCUMENT
    for filename, content, content_type in uploaded_files or []:
      gcs_uri = self.bq_service.upload_patient_document(
          case_id=case_id,
          filename=filename,
          content=content,
          content_type=content_type,
      )
      parsed, doc_telemetry = self.bq_service.parse_and_chunk_patient_document(
          case_id=case_id,
          gcs_uri=gcs_uri,
      )
      parsed_docs.append(parsed)
      telemetry.extend(doc_telemetry)

    # 2. Extract structured patient profile using Gemini
    combined_doc_context = self._format_parsed_docs_for_llm(parsed_docs)
    patient_profile = self._extract_patient_profile(clinical_notes, combined_doc_context)

    biomarkers = patient_profile.get("actionable_biomarkers", [])
    lexical_str = patient_profile.get("pubmed_lexical_biomarkers") or " ".join(biomarkers)
    pubmed_query = patient_profile.get("pubmed_semantic_query") or clinical_notes[:800]
    trials_query = patient_profile.get("clinical_trials_query") or pubmed_query

    # 3. Zero-full-scan Two-Stage Hybrid Retrieval in BigQuery
    raw_pubmed, pubmed_tel = self.bq_service.search_pubmed_hybrid(
        semantic_query=pubmed_query,
        lexical_biomarkers=lexical_str,
        min_pub_year=min_pub_year,
        top_k=top_k_pubmed,
    )
    telemetry.append(pubmed_tel)

    raw_trials, trials_tel = self.bq_service.search_clinical_trials_hybrid(
        semantic_query=trials_query,
        lexical_biomarkers=lexical_str,
        recruiting_only=recruiting_only,
        top_k=top_k_trials,
    )
    telemetry.append(trials_tel)

    private_chunks: list[dict[str, Any]] = []
    if include_private_pdfs:
      try:
        private_chunks, priv_tel = self.bq_service.search_private_pdfs(
            semantic_query=pubmed_query, top_k=5
        )
        telemetry.append(priv_tel)
      except Exception as exc:  # noqa: BLE001
        logger.warning("Private PDFs search skipped: %s", exc)

    # 4. Transparent Multi-Axis Benchmarking
    benchmarked_pubmed = sorted(
        [benchmark_pubmed_article(a, biomarkers) for a in raw_pubmed],
        key=lambda x: x["benchmark"]["total_score"],
        reverse=True,
    )
    benchmarked_trials = sorted(
        [
            benchmark_clinical_trial(
                t,
                biomarkers,
                patient_age_years=patient_profile.get("age_years"),
                patient_sex=patient_profile.get("sex"),
            )
            for t in raw_trials
        ],
        key=lambda x: x["benchmark"]["total_score"],
        reverse=True,
    )

    # 5. Synthesize Clinician-Ready Report with Explanations & References
    markdown_report = self._synthesize_report(
        patient_profile=patient_profile,
        pubmed_articles=benchmarked_pubmed,
        clinical_trials=benchmarked_trials,
        private_chunks=private_chunks,
    )

    total_mb = round(sum(t.get("mb_processed", 0.0) for t in telemetry), 2)

    return {
        "case_id": case_id,
        "patient_profile": patient_profile,
        "parsed_documents_count": len(parsed_docs),
        "benchmarked_pubmed": benchmarked_pubmed,
        "benchmarked_trials": benchmarked_trials,
        "private_pdf_matches": private_chunks,
        "markdown_report": markdown_report,
        "efficiency_telemetry": {
            "total_mb_processed": total_mb,
            "steps": telemetry,
        },
    }

  def _format_parsed_docs_for_llm(self, parsed_docs: list[dict[str, Any]]) -> str:
    if not parsed_docs:
      return "No external PDF/DOCX documents attached."
    parts: list[str] = []
    for doc in parsed_docs:
      fname = doc.get("gcs_uri", "document").split("/")[-1]
      parts.append(f"### Document: {fname}")
      clean_text = (doc.get("clean_text_without_boilerplate") or "")[:15000]
      if clean_text:
        parts.append(clean_text)
      tables = doc.get("extracted_tables") or []
      if tables:
        parts.append("#### Extracted Structured Tables (HTML):")
        for tbl in tables[:10]:
          parts.append(f"[Page {tbl.get('page_number', '?')}]\n{tbl.get('html_table', '')}")
    return "\n\n".join(parts)

  def _extract_patient_profile(
      self, clinical_notes: str, parsed_doc_context: str
  ) -> dict[str, Any]:
    client = _get_genai_client()
    prompt = (
        f"{CASE_EXTRACTION_PROMPT}\n\n"
        f"## Doctor Clinical Notes:\n{clinical_notes}\n\n"
        f"## Parsed Patient Documents (via BigQuery AI.PARSE_DOCUMENT):\n{parsed_doc_context}"
    )
    resp = client.models.generate_content(
        model=settings.gemini_fast_model,
        contents=prompt,
    )
    return _safe_json_loads(resp.text)

  def _synthesize_report(
      self,
      patient_profile: dict[str, Any],
      pubmed_articles: list[dict[str, Any]],
      clinical_trials: list[dict[str, Any]],
      private_chunks: list[dict[str, Any]],
  ) -> str:
    client = _get_genai_client()
    # Trim hydrated text to keep prompt fast & focused
    compact_pubmed = [
        {
            "rank": idx + 1,
            "pmc_id": a.get("pmc_id"),
            "pmid": a.get("pmid"),
            "title": a.get("title"),
            "journal": a.get("journal_name"),
            "pub_year": a.get("pub_year"),
            "pmc_link": a.get("pmc_link"),
            "benchmark": a.get("benchmark"),
            "excerpt": (a.get("hydrated_text_excerpt") or "")[:3000],
        }
        for idx, a in enumerate(pubmed_articles[:8])
    ]
    compact_trials = [
        {
            "rank": idx + 1,
            "nct_id": t.get("nct_id"),
            "title": t.get("brief_title"),
            "status": t.get("overall_status"),
            "phase": t.get("phase"),
            "interventions": t.get("interventions_text"),
            "locations": t.get("locations_summary"),
            "study_url": t.get("study_url"),
            "benchmark": t.get("benchmark"),
            "eligibility_excerpt": (t.get("eligibility_criteria") or "")[:1800],
        }
        for idx, t in enumerate(clinical_trials[:6])
    ]

    payload = {
        "patient_profile": patient_profile,
        "benchmarked_pubmed": compact_pubmed,
        "benchmarked_trials": compact_trials,
        "private_institutional_pdfs": private_chunks[:3],
    }
    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=f"{REPORT_SYNTHESIS_PROMPT}\n\n## Evidence Payload (JSON):\n{json.dumps(payload, default=str)}",
    )
    return resp.text
