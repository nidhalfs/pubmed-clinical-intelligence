-- ============================================================================
-- 04_clinical_trials_schema.sql
-- ClinicalTrials.gov v2 Optimized Table with Autonomous AI.EMBED + Indexes
--
-- OPTIMIZATION:
-- 1. Partitioned by `last_update_date` and clustered by `overall_status, phase`
--    so queries filtering `overall_status IN ('RECRUITING', 'NOT_YET_RECRUITING')`
--    immediately prune closed/terminated trials.
-- 2. Uses Autonomous Embedding Generation (`GENERATED ALWAYS AS (AI.EMBED(...))`)
--    so BigQuery automatically embeds new/updated trials asynchronously with
--    zero manual model management!
-- 3. Backed by BOTH a TREE_AH Vector Index and an inverted Search Index for
--    native hybrid `AI.SEARCH(..., mode => 'AUTO', rerank => TRUE)`.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.clinical_trials` (
  nct_id STRING NOT NULL,
  brief_title STRING,
  official_title STRING,
  overall_status STRING,             -- RECRUITING, ACTIVE_NOT_RECRUITING, COMPLETED, etc.
  phase STRING,                      -- PHASE1, PHASE2, PHASE3, PHASE4, NA
  study_type STRING,                 -- INTERVENTIONAL, OBSERVATIONAL
  lead_sponsor STRING,
  conditions_text STRING,
  interventions_text STRING,
  brief_summary STRING,
  eligibility_criteria STRING,
  min_age STRING,
  max_age STRING,
  sex STRING,
  locations_summary STRING,
  study_url STRING,
  last_update_date DATE,
  searchable_text STRING,            -- Concatenated title + conditions + interventions + eligibility
  -- Autonomous Embedding Generation (BigQuery AI GA)
  embedding STRUCT<result ARRAY<FLOAT64>, status STRING>
    GENERATED ALWAYS AS (
      AI.EMBED(
        searchable_text,
        connection_id => '${BQ_CONNECTION_ID}',
        endpoint => 'text-embedding-005'
      )
    ) STORED OPTIONS (asynchronous = TRUE),
  synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE_TRUNC(last_update_date, YEAR)
CLUSTER BY overall_status, phase, study_type
OPTIONS (
  description = 'Incremental ClinicalTrials.gov v2 dataset with autonomous AI.EMBED and hybrid search indexes.'
);

-- Staging table for idempotent MERGE from the Cloud Run sync job
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.clinical_trials_staging` (
  nct_id STRING NOT NULL,
  brief_title STRING,
  official_title STRING,
  overall_status STRING,
  phase STRING,
  study_type STRING,
  lead_sponsor STRING,
  conditions_text STRING,
  interventions_text STRING,
  brief_summary STRING,
  eligibility_criteria STRING,
  min_age STRING,
  max_age STRING,
  sex STRING,
  locations_summary STRING,
  study_url STRING,
  last_update_date DATE,
  searchable_text STRING
);

-- Create Search Index for exact biomarker / drug / condition matching
CREATE SEARCH INDEX IF NOT EXISTS idx_clinical_trials_lexical
ON `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.clinical_trials`(
  brief_title, conditions_text, interventions_text, eligibility_criteria
);

-- NOTE: Run the vector index creation below once >= 5,000 trials have been synced
-- and autonomous embeddings have populated:
--
-- CREATE VECTOR INDEX IF NOT EXISTS idx_clinical_trials_vec_scann
-- ON `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.clinical_trials`(embedding)
-- STORING (nct_id, brief_title, overall_status, phase, conditions_text, interventions_text, study_url)
-- OPTIONS (
--   index_type = 'TREE_AH',
--   distance_type = 'COSINE'
-- );
