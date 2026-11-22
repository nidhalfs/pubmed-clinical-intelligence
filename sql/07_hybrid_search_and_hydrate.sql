-- ============================================================================
-- 07_hybrid_search_and_hydrate.sql
-- Two-Stage Hybrid Retrieval (Zero Full-Table Scan) for PubMed & ClinicalTrials
--
-- HOW IT AVOIDS FULL-TABLE SCANS:
--   Stage 1A (Semantic): VECTOR_SEARCH on the lean TREE_AH-indexed table
--             (never touches `article_text`).
--   Stage 1B (Lexical):  SEARCH(...) on the inverted Search Index for exact
--             biomarkers / gene fusions / CD markers / drug names.
--   Stage 1C (RRF Fusion): Reciprocal Rank Fusion combines semantic + lexical
--             rankings with journal prestige & study-type priors.
--   Stage 2  (Hydration): Joins `pubmed_articles_cold` ONLY for the final Top-K
--             `pmc_id`s (e.g., 15 articles), reading <0.01% of the corpus!
-- ============================================================================

-- 1. Two-Stage Hybrid Search & Hydration for PubMed
CREATE OR REPLACE PROCEDURE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.sp_search_pubmed_hybrid`(
  in_semantic_query STRING,
  in_lexical_biomarkers STRING,      -- e.g., "FLT3-ITD KMT2A CD33" (space-separated exact terms)
  in_min_pub_year INT64,             -- e.g., 2018 (partition pruning!)
  in_top_k INT64                     -- e.g., 15
)
BEGIN
  WITH query_vec AS (
    SELECT AI.EMBED(
      in_semantic_query,
      connection_id => '${BQ_CONNECTION_ID}',
      endpoint => 'text-embedding-005'
    ).result AS q_embedding
  ),
  -- Stage 1A: Semantic Vector Search on lean indexed HOT table (partition-pruned)
  semantic_hits AS (
    SELECT
      base.pmc_id,
      base.pmid,
      base.title,
      base.author,
      base.journal_name,
      base.pub_year,
      base.study_type_inferred,
      base.abstract_snippet,
      base.pmc_link,
      distance,
      ROW_NUMBER() OVER (ORDER BY distance ASC) AS sem_rank
    FROM VECTOR_SEARCH(
      (
        SELECT *
        FROM `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index`
        WHERE pub_year >= in_min_pub_year
      ),
      'embedding',
      (SELECT q_embedding FROM query_vec),
      top_k => 50,
      distance_type => 'COSINE'
    )
  ),
  -- Stage 1B: Exact Lexical Search via Inverted Search Index (biomarkers, genes, drugs)
  lexical_hits AS (
    SELECT
      pmc_id,
      pmid,
      title,
      author,
      journal_name,
      pub_year,
      study_type_inferred,
      abstract_snippet,
      pmc_link,
      ROW_NUMBER() OVER (ORDER BY pub_year DESC) AS lex_rank
    FROM `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index`
    WHERE pub_year >= in_min_pub_year
      AND in_lexical_biomarkers IS NOT NULL
      AND LENGTH(TRIM(in_lexical_biomarkers)) > 0
      AND SEARCH((title, abstract_snippet), in_lexical_biomarkers)
    LIMIT 60
  ),
  -- Stage 1C: Reciprocal Rank Fusion (RRF) + Journal Impact Enrichment
  fused AS (
    SELECT
      COALESCE(s.pmc_id, l.pmc_id) AS pmc_id,
      COALESCE(s.pmid, l.pmid) AS pmid,
      COALESCE(s.title, l.title) AS title,
      COALESCE(s.author, l.author) AS author,
      COALESCE(s.journal_name, l.journal_name) AS journal_name,
      COALESCE(s.pub_year, l.pub_year) AS pub_year,
      COALESCE(s.study_type_inferred, l.study_type_inferred) AS study_type_inferred,
      COALESCE(s.abstract_snippet, l.abstract_snippet) AS abstract_snippet,
      COALESCE(s.pmc_link, l.pmc_link) AS pmc_link,
      s.distance AS cosine_distance,
      s.sem_rank,
      l.lex_rank,
      -- RRF score (k=60) giving strong boost when BOTH semantic and exact biomarker hit
      (IF(s.sem_rank IS NOT NULL, 1.0 / (60 + s.sem_rank), 0.0)
       + IF(l.lex_rank IS NOT NULL, 1.2 / (60 + l.lex_rank), 0.0)) AS rrf_score,
      CASE
        WHEN s.sem_rank IS NOT NULL AND l.lex_rank IS NOT NULL THEN 'HYBRID_SEMANTIC_AND_EXACT'
        WHEN l.lex_rank IS NOT NULL THEN 'EXACT_BIOMARKER_MATCH'
        ELSE 'SEMANTIC_VECTOR_MATCH'
      END AS retrieval_mode
    FROM semantic_hits s
    FULL OUTER JOIN lexical_hits l
      ON s.pmc_id = l.pmc_id
  ),
  top_candidates AS (
    SELECT
      f.*,
      j.sjr_score,
      j.sjr_quartile,
      j.h_index
    FROM fused f
    LEFT JOIN `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.journal_impact_metrics` j
      ON LOWER(TRIM(f.journal_name)) = j.journal_name_normalized
    QUALIFY ROW_NUMBER() OVER (ORDER BY f.rrf_score DESC) <= in_top_k
  )
  -- Stage 2: Selective Hydration of full article_text ONLY for the Top-K winners
  SELECT
    tc.pmc_id,
    tc.pmid,
    tc.title,
    tc.author,
    tc.journal_name,
    tc.pub_year,
    tc.study_type_inferred,
    tc.pmc_link,
    tc.cosine_distance,
    tc.rrf_score,
    tc.retrieval_mode,
    COALESCE(tc.sjr_score, 0.0) AS sjr_score,
    COALESCE(tc.sjr_quartile, 'UNRANKED') AS sjr_quartile,
    COALESCE(LEFT(cold.article_text, 12000), tc.abstract_snippet) AS hydrated_text_excerpt
  FROM top_candidates tc
  LEFT JOIN `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_articles_cold` cold
    ON tc.pmc_id = cold.pmc_id
  ORDER BY tc.rrf_score DESC;
END;


-- 2. Hybrid Search for Recruiting / Active Clinical Trials (Cluster-Pruned)
CREATE OR REPLACE PROCEDURE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.sp_search_clinical_trials_hybrid`(
  in_semantic_query STRING,
  in_lexical_biomarkers STRING,
  in_recruiting_only BOOL,
  in_top_k INT64
)
BEGIN
  WITH status_filtered AS (
    SELECT *
    FROM `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.clinical_trials`
    WHERE (
      NOT in_recruiting_only
      OR overall_status IN ('RECRUITING', 'NOT_YET_RECRUITING', 'ENROLLING_BY_INVITATION')
    )
  ),
  -- Native AI.SEARCH (hybrid vector + lexical + optional rerank when autonomous embeddings exist)
  semantic_trials AS (
    SELECT
      base.nct_id,
      base.brief_title,
      base.overall_status,
      base.phase,
      base.study_type,
      base.lead_sponsor,
      base.conditions_text,
      base.interventions_text,
      base.brief_summary,
      base.eligibility_criteria,
      base.min_age,
      base.max_age,
      base.sex,
      base.locations_summary,
      base.study_url,
      base.last_update_date,
      distance,
      ROW_NUMBER() OVER (ORDER BY distance ASC) AS sem_rank
    FROM AI.SEARCH(
      (SELECT * FROM status_filtered),
      'searchable_text',
      in_semantic_query,
      top_k => 40,
      mode => 'AUTO'
    )
  ),
  lexical_trials AS (
    SELECT
      nct_id,
      brief_title,
      overall_status,
      phase,
      study_type,
      lead_sponsor,
      conditions_text,
      interventions_text,
      brief_summary,
      eligibility_criteria,
      min_age,
      max_age,
      sex,
      locations_summary,
      study_url,
      last_update_date,
      ROW_NUMBER() OVER (ORDER BY last_update_date DESC) AS lex_rank
    FROM status_filtered
    WHERE in_lexical_biomarkers IS NOT NULL
      AND LENGTH(TRIM(in_lexical_biomarkers)) > 0
      AND SEARCH((brief_title, conditions_text, interventions_text, eligibility_criteria), in_lexical_biomarkers)
    LIMIT 40
  )
  SELECT
    COALESCE(s.nct_id, l.nct_id) AS nct_id,
    COALESCE(s.brief_title, l.brief_title) AS brief_title,
    COALESCE(s.overall_status, l.overall_status) AS overall_status,
    COALESCE(s.phase, l.phase) AS phase,
    COALESCE(s.study_type, l.study_type) AS study_type,
    COALESCE(s.lead_sponsor, l.lead_sponsor) AS lead_sponsor,
    COALESCE(s.conditions_text, l.conditions_text) AS conditions_text,
    COALESCE(s.interventions_text, l.interventions_text) AS interventions_text,
    COALESCE(s.brief_summary, l.brief_summary) AS brief_summary,
    COALESCE(s.eligibility_criteria, l.eligibility_criteria) AS eligibility_criteria,
    COALESCE(s.min_age, l.min_age) AS min_age,
    COALESCE(s.max_age, l.max_age) AS max_age,
    COALESCE(s.sex, l.sex) AS sex,
    COALESCE(s.locations_summary, l.locations_summary) AS locations_summary,
    COALESCE(s.study_url, l.study_url) AS study_url,
    COALESCE(s.last_update_date, l.last_update_date) AS last_update_date,
    s.distance AS cosine_distance,
    (IF(s.sem_rank IS NOT NULL, 1.0 / (60 + s.sem_rank), 0.0)
     + IF(l.lex_rank IS NOT NULL, 1.2 / (60 + l.lex_rank), 0.0)) AS rrf_score,
    CASE
      WHEN s.sem_rank IS NOT NULL AND l.lex_rank IS NOT NULL THEN 'HYBRID_SEMANTIC_AND_EXACT'
      WHEN l.lex_rank IS NOT NULL THEN 'EXACT_BIOMARKER_MATCH'
      ELSE 'SEMANTIC_VECTOR_MATCH'
    END AS retrieval_mode
  FROM semantic_trials s
  FULL OUTER JOIN lexical_trials l
    ON s.nct_id = l.nct_id
  QUALIFY ROW_NUMBER() OVER (
    ORDER BY (
      IF(s.sem_rank IS NOT NULL, 1.0 / (60 + s.sem_rank), 0.0)
      + IF(l.lex_rank IS NOT NULL, 1.2 / (60 + l.lex_rank), 0.0)
    ) DESC
  ) <= in_top_k
  ORDER BY rrf_score DESC;
END;
