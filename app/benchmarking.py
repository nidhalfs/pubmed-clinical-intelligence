"""Transparent Multi-Axis Evidence Benchmarking Engine.

Scores retrieved PubMed literature and ClinicalTrials.gov studies against a
structured patient profile with explicit, auditable sub-scores so clinicians
and researchers can verify *why* each reference was selected and ranked.
"""

from __future__ import annotations

import datetime
import re
from typing import Any


EVIDENCE_HIERARCHY_POINTS: dict[str, tuple[float, str]] = {
    "META_ANALYSIS": (30.0, "Level 1A — Systematic Review / Meta-Analysis"),
    "CLINICAL_TRIAL": (26.0, "Level 1B/2 — Interventional Clinical Trial"),
    "COHORT_STUDY": (20.0, "Level 2B — Cohort / Observational Study"),
    "REVIEW": (15.0, "Level 3 — Narrative / Clinical Review"),
    "CASE_REPORT": (12.0, "Level 4 — Case Report / Case Series"),
    "OTHER": (8.0, "Level 5 — Preclinical / Other Research"),
}

TRIAL_STATUS_POINTS: dict[str, float] = {
    "RECRUITING": 30.0,
    "NOT_YET_RECRUITING": 22.0,
    "ENROLLING_BY_INVITATION": 18.0,
    "ACTIVE_NOT_RECRUITING": 8.0,
}


def _find_matched_terms(text: str, terms: list[str]) -> list[str]:
  if not text or not terms:
    return []
  lowered = text.lower()
  matched = []
  for term in terms:
    cleaned = term.strip()
    if cleaned and cleaned.lower() in lowered:
      matched.append(cleaned)
  return matched


def benchmark_pubmed_article(
    article: dict[str, Any],
    patient_biomarkers: list[str],
    current_year: int | None = None,
) -> dict[str, Any]:
  """Computes a 0-100 transparent benchmark scorecard for a PubMed article."""
  year_now = current_year or datetime.date.today().year
  searchable = " ".join([
      str(article.get("title") or ""),
      str(article.get("hydrated_text_excerpt") or article.get("abstract_snippet") or ""),
  ])

  # 1. Biomarker & Semantic Match (max 40 pts)
  matched_biomarkers = _find_matched_terms(searchable, patient_biomarkers)
  biomarker_ratio = (
      len(matched_biomarkers) / len(patient_biomarkers)
      if patient_biomarkers
      else 0.0
  )
  cosine_dist = article.get("cosine_distance")
  semantic_sim = max(0.0, min(1.0, 1.0 - float(cosine_dist))) if cosine_dist is not None else 0.65
  retrieval_mode = str(article.get("retrieval_mode") or "SEMANTIC_VECTOR_MATCH")
  hybrid_bonus = 4.0 if retrieval_mode == "HYBRID_SEMANTIC_AND_EXACT" else 0.0
  match_score = round(min(40.0, (semantic_sim * 20.0) + (biomarker_ratio * 16.0) + hybrid_bonus), 1)

  # 2. Evidence Level / Study Design Rigor (max 30 pts)
  study_type = str(article.get("study_type_inferred") or "OTHER").upper()
  evidence_score, evidence_label = EVIDENCE_HIERARCHY_POINTS.get(
      study_type, EVIDENCE_HIERARCHY_POINTS["OTHER"]
  )

  # 3. Journal Prestige / SCImago SJR (max 15 pts)
  sjr = float(article.get("sjr_score") or 0.0)
  quartile = str(article.get("sjr_quartile") or "UNRANKED").upper()
  if sjr >= 10.0:
    journal_score = 15.0
  elif sjr >= 4.0:
    journal_score = 12.5
  elif sjr >= 1.5 or quartile == "Q1":
    journal_score = 10.0
  elif quartile == "Q2":
    journal_score = 7.0
  elif quartile in ("Q3", "Q4"):
    journal_score = 4.5
  else:
    journal_score = 5.0

  # 4. Recency (max 15 pts)
  pub_year = int(article.get("pub_year") or 2020)
  age = max(0, year_now - pub_year)
  recency_score = round(max(3.0, 15.0 - (age * 1.5)), 1)

  total_score = round(match_score + evidence_score + journal_score + recency_score, 1)

  return {
      **article,
      "benchmark": {
          "total_score": total_score,
          "breakdown": {
              "biomarker_and_semantic_match": {"score": match_score, "max": 40},
              "evidence_hierarchy_rigor": {
                  "score": evidence_score,
                  "max": 30,
                  "level": evidence_label,
              },
              "journal_impact": {
                  "score": journal_score,
                  "max": 15,
                  "sjr_score": sjr,
                  "quartile": quartile,
              },
              "recency": {"score": recency_score, "max": 15, "pub_year": pub_year},
          },
          "matched_biomarkers": matched_biomarkers,
          "retrieval_mode": retrieval_mode,
      },
  }


def _parse_age_years(age_str: str | None) -> float | None:
  if not age_str:
    return None
  m = re.search(r"(\d+(?:\.\d+)?)\s*(year|month|week|day)", age_str.lower())
  if not m:
    return None
  val = float(m.group(1))
  unit = m.group(2)
  if unit == "year":
    return val
  if unit == "month":
    return val / 12.0
  if unit == "week":
    return val / 52.0
  return val / 365.0


def benchmark_clinical_trial(
    trial: dict[str, Any],
    patient_biomarkers: list[str],
    patient_age_years: float | None = None,
    patient_sex: str | None = None,
) -> dict[str, Any]:
  """Computes a 0-100 transparent benchmark scorecard + eligibility checklist for a trial."""
  searchable = " ".join([
      str(trial.get("brief_title") or ""),
      str(trial.get("conditions_text") or ""),
      str(trial.get("interventions_text") or ""),
      str(trial.get("eligibility_criteria") or ""),
  ])

  # 1. Recruitment Actionability (max 30 pts)
  status = str(trial.get("overall_status") or "UNKNOWN").upper()
  status_score = TRIAL_STATUS_POINTS.get(status, 4.0)

  # 2. Biomarker & Semantic Match (max 35 pts)
  matched_biomarkers = _find_matched_terms(searchable, patient_biomarkers)
  biomarker_ratio = (
      len(matched_biomarkers) / len(patient_biomarkers)
      if patient_biomarkers
      else 0.0
  )
  cosine_dist = trial.get("cosine_distance")
  semantic_sim = max(0.0, min(1.0, 1.0 - float(cosine_dist))) if cosine_dist is not None else 0.65
  retrieval_mode = str(trial.get("retrieval_mode") or "SEMANTIC_VECTOR_MATCH")
  hybrid_bonus = 4.0 if retrieval_mode == "HYBRID_SEMANTIC_AND_EXACT" else 0.0
  match_score = round(min(35.0, (semantic_sim * 16.0) + (biomarker_ratio * 15.0) + hybrid_bonus), 1)

  # 3. Phase Maturity (max 20 pts)
  phase = str(trial.get("phase") or "NA").upper()
  if "PHASE3" in phase or "PHASE4" in phase:
    phase_score = 20.0
  elif "PHASE2" in phase and "PHASE3" in phase:
    phase_score = 18.0
  elif "PHASE2" in phase:
    phase_score = 15.0
  elif "PHASE1" in phase:
    phase_score = 11.0
  else:
    phase_score = 8.0

  # 4. Deterministic Eligibility Pre-Check (max 15 pts)
  checklist: list[dict[str, str]] = []
  elig_score = 15.0

  min_age = _parse_age_years(trial.get("min_age"))
  max_age = _parse_age_years(trial.get("max_age"))
  if patient_age_years is not None:
    if (min_age is not None and patient_age_years < min_age) or (
        max_age is not None and patient_age_years > max_age
    ):
      checklist.append({
          "criterion": f"Age range ({trial.get('min_age') or 'Any'} - {trial.get('max_age') or 'Any'})",
          "status": "FAIL",
          "detail": f"Patient age ({patient_age_years} yrs) falls outside trial bounds.",
      })
      elig_score -= 10.0
    else:
      checklist.append({
          "criterion": f"Age range ({trial.get('min_age') or 'Any'} - {trial.get('max_age') or 'Any'})",
          "status": "PASS",
          "detail": f"Patient age ({patient_age_years} yrs) is eligible.",
      })
  else:
    checklist.append({
        "criterion": f"Age range ({trial.get('min_age') or 'Any'} - {trial.get('max_age') or 'Any'})",
        "status": "UNKNOWN",
        "detail": "Patient age not specified in profile.",
    })

  trial_sex = str(trial.get("sex") or "ALL").upper()
  if patient_sex and trial_sex != "ALL" and patient_sex.upper() != trial_sex:
    checklist.append({
        "criterion": f"Sex ({trial_sex})",
        "status": "FAIL",
        "detail": f"Trial restricts to {trial_sex}.",
    })
    elig_score -= 5.0
  else:
    checklist.append({
        "criterion": f"Sex ({trial_sex})",
        "status": "PASS",
        "detail": "Eligible.",
    })

  if matched_biomarkers:
    checklist.append({
        "criterion": "Biomarker / Target Mention",
        "status": "PASS",
        "detail": f"Matches: {', '.join(matched_biomarkers)}",
    })
  else:
    checklist.append({
        "criterion": "Biomarker / Target Mention",
        "status": "REVIEW",
        "detail": "Matched semantically; verify exact molecular criteria in eligibility text.",
    })
    elig_score = max(0.0, elig_score - 3.0)

  total_score = round(status_score + match_score + phase_score + max(0.0, elig_score), 1)

  return {
      **trial,
      "benchmark": {
          "total_score": total_score,
          "breakdown": {
              "recruitment_actionability": {"score": status_score, "max": 30, "status": status},
              "biomarker_and_semantic_match": {"score": match_score, "max": 35},
              "phase_maturity": {"score": phase_score, "max": 20, "phase": phase},
              "eligibility_alignment": {"score": max(0.0, elig_score), "max": 15},
          },
          "matched_biomarkers": matched_biomarkers,
          "eligibility_checklist": checklist,
          "retrieval_mode": retrieval_mode,
      },
  }
