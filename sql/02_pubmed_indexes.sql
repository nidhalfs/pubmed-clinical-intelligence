-- ============================================================================
-- 02_pubmed_indexes.sql
-- Vector Index (TREE_AH / ScaNN) + Inverted Search Index on `pubmed_search_index`
--
-- NOTE: BigQuery Vector Indexes require >= 5,000 rows in the base table to
-- build and become active. Run 03_pubmed_incremental_sync.sql first!
-- ============================================================================

-- 1. TREE_AH (ScaNN) Vector Index with STORING columns
-- Storing key metadata columns directly in the vector index allows BigQuery
-- to return candidate matches WITHOUT reading the base table blocks!
CREATE VECTOR INDEX IF NOT EXISTS idx_pubmed_vec_scann
ON `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index`(embedding)
STORING (pmc_id, pmid, title, pub_year, study_type_inferred, journal_name, pmc_link)
OPTIONS (
  index_type = 'TREE_AH',
  distance_type = 'COSINE',
  tree_ah_options = '{"leaf_node_embedding_count": 1000}'
);

-- 2. Inverted Search Index for exact clinical biomarkers, gene fusions, CD markers, and drugs
-- Solves the biggest weakness of pure vector search (e.g., exact matching "FLT3-ITD",
-- "KMT2A-AF6", "CD123", "Selinexor", "Venetoclax") without full-table scans.
CREATE SEARCH INDEX IF NOT EXISTS idx_pubmed_lexical
ON `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.pubmed_search_index`(title, abstract_snippet, journal_name);
