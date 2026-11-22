# PubMed & ClinicalTrials Clinical Case Intelligence on BigQuery AI

A customer-deployable Google Cloud solution that combines:
1. **Part 1 — Optimized PubMed & ClinicalTrials.gov BigQuery Data Layer (Zero Full-Table Scans):**
   * **Hot/Cold Table Split** over `bigquery-public-data.pmc_open_access_commercial`: separates lean metadata + embeddings (`pubmed_search_index`, range-partitioned by publication year & clustered by study type) from full `article_text` (`pubmed_articles_cold`).
   * **Hybrid Indexing:** `TREE_AH` (ScaNN) Vector Index with `STORING(...)` columns + BigQuery Inverted Search Index for exact clinical biomarkers (`FLT3-ITD`, `KMT2A-AF6`, `CD33`, `CD123`). Full text is hydrated *only* for the Top-K winning PMIDs — cutting bytes scanned by **95–99%+**.
   * **ClinicalTrials.gov v2 Incremental Pipeline:** Cloud Run Job syncing active/recruiting trials into a cluster-pruned BigQuery table with **Autonomous `AI.EMBED`** and native hybrid **`AI.SEARCH(..., mode => 'AUTO')`**.
2. **Part 2 — Clinician/Researcher Document Intelligence & Benchmarked Report App:**
   * **BigQuery AI Document Intelligence (GA):** Doctor-uploaded patient case PDFs/DOCX/PPTX (pathology, NGS panels, flow cytometry, discharge summaries) are parsed natively in BigQuery using **`AI.PARSE_DOCUMENT`** (`layout-v1` for structured HTML lab tables, boilerplate removal, and page/bounding-box citations) and **`AI.CHUNK_DOCUMENT`** (layout-aware semantic chunking) — zero external OCR/chunking infrastructure.
   * **Optional Customer Private PDFs:** Drop institutional protocols or internal case series into GCS and query them alongside PubMed & ClinicalTrials via `AI.CHUNK_DOCUMENT` + `AI.EMBED` + `AI.SEARCH`.
   * **Google ADK Multi-Agent Pipeline & FastAPI Web UI (Cloud Run):** Extracts structured patient profiles with source page citations, runs two-stage hybrid retrieval, applies a **transparent 0–100 multi-axis benchmarking engine**, and produces an exportable clinician report with clickable PMID/PMC/NCT links and BigQuery bytes-scanned telemetry.

See [ARCHITECTURE_ANALYSIS.md](ARCHITECTURE_ANALYSIS.md) for the detailed Option 1 vs. Option 2 trade-off analysis.

---

## Quickstart: Deploy to Any Customer GCP Project

Prerequisites: `gcloud`, `bq`, `terraform` (>= 1.5), `envsubst`, and a GCP project with Billing enabled.

```bash
export GCP_PROJECT_ID="your-gcp-project-id"
export GCP_REGION="us-central1"
export BQ_LOCATION="US"
export BQ_DATASET_ID="clinical_intelligence"

chmod +x deploy.sh
./deploy.sh
```

> **Note on Regional Availability:** `AI.PARSE_DOCUMENT` and `AI.CHUNK_DOCUMENT` are GA in `US` and `EU` multi-regions and most US/EU single regions (except `europe-west2` and `europe-west6`).

---

## Local Preview (Offline Tumor Board Demo)

You can preview the Clinician UI and transparent benchmarking engine locally without GCP credentials:

```bash
cd app
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

Open `http://localhost:8080` and click **Load Tumor Board Demo**.

---

## Running Unit Tests

```bash
cd app
pytest -v
```

---

## Disclaimer
This solution is intended strictly for biomedical research and educational decision-support. It does not provide autonomous medical advice or clinical diagnosis. Always verify primary PMID, PMC, and ClinicalTrials.gov sources.
