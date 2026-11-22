-- ============================================================================
-- 03_pubmed_incremental_sync.sql
-- Incremental Sync from `bigquery-public-data.pmc_open_access_commercial.articles`
--
-- Schedule this weekly via BigQuery Scheduled Queries.
-- Set `sync_limit` for initial testing/dev (e.g., 50000) or remove LIMIT for
-- full corpus materialization (2M+ rows).
-- ============================================================================

DECLARE sync_year_min INT64 DEFAULT 2015;  -- Focus on modern biomedical literature; lower to 1950 for full archive

-- 1. Sync lean metadata + embeddings into HOT `pubmed_search_index`
MERGE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index` T
USING (
  WITH parsed_source AS (
    SELECT
      pmc_id,
      pmid,
      title,
      author,
      article_citation,
      -- Extract journal name from citation prefix (before first period)
      TRIM(SPLIT(IFNULL(article_citation, ''), '.')[SAFE_OFFSET(0)]) AS journal_name,
      -- Extract 4-digit publication year from citation
      COALESCE(
        SAFE_CAST(REGEXP_EXTRACT(IFNULL(article_citation, ''), r'\b(19[5-9]\d|20[0-3]\d)\b') AS INT64),
        2020
      ) AS pub_year,
      -- Heuristic study-type classifier for evidence hierarchy benchmarking & clustering
      CASE
        WHEN REGEXP_CONTAINS(LOWER(IFNULL(title, '')), r'meta-analysis|systematic review') THEN 'META_ANALYSIS'
        WHEN REGEXP_CONTAINS(LOWER(IFNULL(title, '') || ' ' || LEFT(IFNULL(article_text, ''), 1200)), r'phase\s*(i|ii|iii|iv|1|2|3)|randomized controlled|clinical trial|nct\d{8}') THEN 'CLINICAL_TRIAL'
        WHEN REGEXP_CONTAINS(LOWER(IFNULL(title, '')), r'case report|case series') THEN 'CASE_REPORT'
        WHEN REGEXP_CONTAINS(LOWER(IFNULL(title, '')), r'cohort|retrospective|prospective|multicenter') THEN 'COHORT_STUDY'
        WHEN REGEXP_CONTAINS(LOWER(IFNULL(title, '')), r'\breview\b') THEN 'REVIEW'
        ELSE 'OTHER'
      END AS study_type_inferred,
      LEFT(IFNULL(article_text, ''), 2000) AS abstract_snippet,
      pmc_link,
      ml_generate_embedding_result AS embedding
    FROM `bigquery-public-data.pmc_open_access_commercial.articles`
    WHERE pmc_id IS NOT NULL
      AND ARRAY_LENGTH(ml_generate_embedding_result) > 0
  )
  SELECT *
  FROM parsed_source
  WHERE pub_year >= sync_year_min
) S
ON T.pmc_id = S.pmc_id
WHEN NOT MATCHED THEN
  INSERT (
    pmc_id, pmid, title, author, article_citation, journal_name,
    pub_year, study_type_inferred, abstract_snippet, pmc_link, embedding, synced_at
  )
  VALUES (
    S.pmc_id, S.pmid, S.title, S.author, S.article_citation, S.journal_name,
    S.pub_year, S.study_type_inferred, S.abstract_snippet, S.pmc_link, S.embedding, CURRENT_TIMESTAMP()
  );

-- 2. Sync full text into COLD `pubmed_articles_cold` (clustered by pmc_id)
MERGE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_articles_cold` T
USING (
  SELECT
    src.pmc_id,
    src.pmid,
    src.article_text
  FROM `bigquery-public-data.pmc_open_access_commercial.articles` src
  INNER JOIN `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index` idx
    ON src.pmc_id = idx.pmc_id
) S
ON T.pmc_id = S.pmc_id
WHEN NOT MATCHED THEN
  INSERT (pmc_id, pmid, article_text, synced_at)
  VALUES (S.pmc_id, S.pmid, S.article_text, CURRENT_TIMESTAMP());
