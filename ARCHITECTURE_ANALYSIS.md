# PubMed & ClinicalTrials BigQuery AI Optimization Analysis

## Executive Verdict: Option 1 vs. Option 2

| Dimension | Option 1: Structured Public Data (`pmc_open_access_commercial` + ClinicalTrials.gov v2) | Option 2: Unstructured Documents (`AI.PARSE_DOCUMENT` + `AI.CHUNK_DOCUMENT`) |
| :--- | :--- | :--- |
| **Best Used For** | Millions of public PubMed Central articles & 450K+ ClinicalTrials.gov studies. | **Doctor-submitted patient case files** (PDF/DOCX pathology reports, NGS panels, lab tables, discharge summaries) and **optional customer private PDFs** (institutional protocols, internal case series). |
| **Why?** | Already machine-readable XML/JSON with pre-computed text and metadata. Running Document AI on 2M+ PubMed PDFs (~20M pages) would add massive Document AI per-page costs for zero structural gain. | Eliminates bespoke OCR/Document AI/LangChain chunking pipelines. Preserves clinical tables as HTML (`layout-v1`), strips headers/footers, provides page + bounding-box grounding, and produces layout-aware semantic chunks (`AI.CHUNK_DOCUMENT`). |
| **BigQuery AI Upgrade** | **Hot/Cold Table Split** + **`TREE_AH` (ScaNN) Vector Index** + **Search Index** + **Autonomous `AI.EMBED`** + **`AI.SEARCH(..., mode => 'AUTO', rerank => TRUE)`**. | Direct SQL-native **`AI.PARSE_DOCUMENT`** + **`AI.CHUNK_DOCUMENT`** + **Autonomous `AI.EMBED`** + **`AI.SEARCH`**. |

### Conclusion
The **optimal architecture combines both**:
1. **Optimize PubMed & ClinicalTrials.gov** in BigQuery using **Hot/Cold table separation, `TREE_AH` Vector Indexes, Search Indexes, Partitioning/Clustering, Autonomous `AI.EMBED`, and hybrid `AI.SEARCH`** so doctor queries **never scan the full PubMed corpus**.
2. **Use the newly GA `AI.PARSE_DOCUMENT` and `AI.CHUNK_DOCUMENT`** for **patient case document ingestion** (extracting structured lab/NGS tables and page citations with zero external infra) and **optional customer private PDFs** layered seamlessly into the same BigQuery RAG index.

---

## Why the Current PubMed BigQuery Solution Needs Optimization

The reference pattern in the Dec 2025 blog and `google/pubmed-rag` queries `bigquery-public-data.pmc_open_access_commercial.articles` directly with legacy `ML.GENERATE_EMBEDDING` + `VECTOR_SEARCH`:

1. **Full-Table / Wide-Column Scans:** `articles` stores full `article_text` alongside embeddings across 2M+ rows (hundreds of GBs to TBs). Running unindexed `VECTOR_SEARCH` or `REGEXP_CONTAINS` on `article_text` scans massive byte volumes on every single query.
2. **Pure Vector Misses Exact Biomarkers:** Semantic-only search can miss exact clinical tokens like `FLT3-ITD`, `KMT2A-AF6`, `CD33+`, `CD123`, or exact drug/protocol names (`NOPHO DBH AML 2012`). Combining a **Vector Index** with a **BigQuery Search Index** (or `AI.SEARCH(..., mode => 'AUTO', rerank => TRUE)`) solves both semantic conceptual matching and exact biomarker precision.
3. **No ClinicalTrials.gov Integration:** Doctors evaluating refractory/rare cases need *recruiting* trials matched against structured eligibility criteria (age, biomarkers, prior therapies, organ function), not just published papers.
4. **No Native Patient Document Parsing:** Clinicians have multi-page PDFs/DOCX (NGS panels, immunophenotyping, pathology, discharge summaries) rather than manually typed text prompts.
5. **Limited Benchmarking Transparency:** Clinical teams need explicit, auditable explanations of *why* a paper or trial ranked high (biomarker match, study design hierarchy, journal impact, eligibility checklist) and *where* in the patient's documents each clinical fact came from.

---

## Optimization Impact Summary

| Optimization | Mechanism | Cost / Latency Impact |
| :--- | :--- | :--- |
| **Hot/Cold Split** | Separate lean `pubmed_search_index` (metadata + embedding) from `article_text` | **95–99% reduction** in bytes scanned during search; full text hydrated only for Top-K PMIDs |
| **`TREE_AH` (ScaNN) Vector Index + `STORING`** | Approximate nearest neighbor index with stored metadata columns | Sub-second candidate retrieval without touching base table blocks |
| **BigQuery Search Index** | Tokenized inverted index on `title`, `abstract_snippet`, `keywords` | Fast exact-match pre-filtering for genes, mutations, CD markers, drugs |
| **Partitioning & Clustering** | Partition by `pub_year_date` / `last_update_date`; cluster by `overall_status`, `phase`, `study_type` | Immediate partition/block pruning (e.g., `overall_status = 'RECRUITING'`) |
| **Autonomous `AI.EMBED` + `AI.SEARCH`** | `GENERATED ALWAYS AS (AI.EMBED(...)) STORED OPTIONS(asynchronous = TRUE)` | Zero manual embedding model management; native hybrid search + cross-encoder reranking |
| **`AI.PARSE_DOCUMENT` + `AI.CHUNK_DOCUMENT`** | Auto-managed `bq-created-layout-parser` in SQL | Zero OCR/chunking infra; HTML table extraction + page/bounding-box citations |
