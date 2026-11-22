-- ============================================================================
-- 01_pubmed_optimized_tables.sql
-- Part 1: Hot/Cold Split for PubMed Central Open Access in BigQuery
--
-- WHY:
-- `bigquery-public-data.pmc_open_access_commercial.articles` stores full
-- `article_text` + embeddings in one wide table (hundreds of GBs to TBs).
-- Querying it directly forces massive scans on every clinician request.
--
-- OPTIMIZATION:
-- 1. `pubmed_search_index` (HOT): Lean metadata + compact abstract snippet +
--    embedding ONLY (no `article_text`). Range-partitioned by `pub_year` and
--    clustered by `study_type_inferred, pmc_id`.
-- 2. `pubmed_articles_cold` (COLD): Full `article_text` clustered by `pmc_id`
--    for selective Top-K point-lookup hydration ONLY after ranking.
-- 3. `journal_impact_metrics`: SCImago / Journal prestige metadata for
--    transparent evidence benchmarking.
-- ============================================================================

-- 1. HOT SEARCH TABLE (Lean metadata + embedding, NO full article_text)
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index` (
  pmc_id STRING NOT NULL,
  pmid STRING,
  title STRING,
  author STRING,
  article_citation STRING,
  journal_name STRING,
  pub_year INT64,
  study_type_inferred STRING,        -- META_ANALYSIS, CLINICAL_TRIAL, COHORT_STUDY, CASE_REPORT, REVIEW, OTHER
  abstract_snippet STRING,           -- First ~2,000 chars for fast lexical search & preview
  pmc_link STRING,
  embedding ARRAY<FLOAT64>,          -- Pre-computed embedding synced from public dataset
  synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY RANGE_BUCKET(pub_year, GENERATE_ARRAY(1950, 2035, 1))
CLUSTER BY study_type_inferred, pmc_id
OPTIONS (
  description = 'Optimized lean PubMed search table (Hot layer) without full article_text to eliminate full-table scans.'
);

-- 2. COLD HYDRATION TABLE (Full article_text, clustered by pmc_id for point lookups)
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_articles_cold` (
  pmc_id STRING NOT NULL,
  pmid STRING,
  article_text STRING,
  synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY pmc_id, pmid
OPTIONS (
  description = 'Cold hydration table storing full PubMed article_text. Queried ONLY by exact pmc_id IN (...) for Top-K winners.'
);

-- 3. JOURNAL IMPACT REFERENCE TABLE (For transparent evidence benchmarking)
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.journal_impact_metrics` (
  journal_name_normalized STRING NOT NULL,
  journal_title STRING,
  sjr_score FLOAT64,                 -- SCImago Journal Rank
  sjr_quartile STRING,               -- Q1, Q2, Q3, Q4
  h_index INT64
)
CLUSTER BY journal_name_normalized
OPTIONS (
  description = 'Journal prestige & impact metrics (e.g., SCImago SJR) for benchmarking retrieved PubMed literature.'
);

-- Seed high-impact medical journals (customers can bulk-load full SCImago CSV into this table)
MERGE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.journal_impact_metrics` T
USING (
  SELECT * FROM UNNEST([
    STRUCT('n engl j med' AS journal_name_normalized, 'New England Journal of Medicine' AS journal_title, 20.5 AS sjr_score, 'Q1' AS sjr_quartile, 1130 AS h_index),
    STRUCT('lancet', 'The Lancet', 14.8, 'Q1', 895),
    STRUCT('jama', 'JAMA', 11.2, 'Q1', 756),
    STRUCT('nature', 'Nature', 18.5, 'Q1', 1391),
    STRUCT('science', 'Science', 15.1, 'Q1', 1336),
    STRUCT('cell', 'Cell', 24.3, 'Q1', 892),
    STRUCT('nat med', 'Nature Medicine', 16.9, 'Q1', 630),
    STRUCT('j clin oncol', 'Journal of Clinical Oncology', 9.4, 'Q1', 610),
    STRUCT('blood', 'Blood', 6.8, 'Q1', 530),
    STRUCT('lancet oncol', 'The Lancet Oncology', 13.5, 'Q1', 410),
    STRUCT('leukemia', 'Leukemia', 4.6, 'Q1', 255),
    STRUCT('haematologica', 'Haematologica', 3.4, 'Q1', 195),
    STRUCT('cancer discov', 'Cancer Discovery', 11.8, 'Q1', 210),
    STRUCT('ann oncol', 'Annals of Oncology', 8.9, 'Q1', 310),
    STRUCT('bmj', 'BMJ', 4.9, 'Q1', 495),
    STRUCT('plos one', 'PLoS ONE', 0.89, 'Q1', 435),
    STRUCT('front oncol', 'Frontiers in Oncology', 1.15, 'Q2', 120),
    STRUCT('cancers (basel)', 'Cancers', 1.32, 'Q1', 115)
  ])
) S
ON T.journal_name_normalized = S.journal_name_normalized
WHEN NOT MATCHED THEN
  INSERT (journal_name_normalized, journal_title, sjr_score, sjr_quartile, h_index)
  VALUES (S.journal_name_normalized, S.journal_title, S.sjr_score, S.sjr_quartile, S.h_index);
