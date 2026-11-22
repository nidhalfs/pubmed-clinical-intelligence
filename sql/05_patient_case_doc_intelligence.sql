-- ============================================================================
-- 05_patient_case_doc_intelligence.sql
-- BigQuery AI Document Intelligence (GA): AI.PARSE_DOCUMENT & AI.CHUNK_DOCUMENT
--
-- Uses the newly GA Document Intelligence SQL functions to ingest doctor-uploaded
-- patient case documents (PDF/DOCX/PPTX: NGS panels, pathology reports, labs,
-- discharge summaries) with ZERO external OCR or chunking infrastructure:
--   1. AI.PARSE_DOCUMENT(..., schema: 'layout-v1') -> Extracts reading-order
--      blocks, structured HTML tables (labs/immunophenotyping), page numbers,
--      and bounding boxes while stripping repetitive headers/footers.
--   2. AI.PARSE_DOCUMENT(..., schema: 'raw-docai') -> Chained directly into
--      AI.CHUNK_DOCUMENT(..., chunk_size => 1000) for layout-aware semantic
--      chunks preserving multi-page clinical narrative & tables.
-- ============================================================================

-- 1. Patient case submission registry
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.patient_cases` (
  case_id STRING NOT NULL,
  submitted_by STRING,
  clinical_notes STRING,
  structured_profile JSON,           -- Extracted diagnosis, biomarkers, labs, prior_therapies, exclusions
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(created_at)
CLUSTER BY case_id;

-- 2. Parsed layout blocks from AI.PARSE_DOCUMENT (layout-v1) for visual grounding & HTML tables
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.patient_case_parsed_docs` (
  case_id STRING NOT NULL,
  gcs_uri STRING NOT NULL,
  parsed_layout_v1 JSON,             -- Flat blocks with page_number, type ('table','paragraph','heading'), bounding boxes
  extracted_tables ARRAY<STRUCT<page_number INT64, html_table STRING>>,
  clean_text_without_boilerplate STRING,
  parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(parsed_at)
CLUSTER BY case_id;

-- 3. Layout-aware semantic chunks from AI.CHUNK_DOCUMENT (chained from raw-docai)
CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.patient_case_chunks` (
  case_id STRING NOT NULL,
  gcs_uri STRING NOT NULL,
  chunk_id INT64,
  start_page INT64,
  end_page INT64,
  content STRING,
  chunked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(chunked_at)
CLUSTER BY case_id;

-- 4. Stored Procedure: Parse + Extract Tables + Chunk a patient document in SQL
CREATE OR REPLACE PROCEDURE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.sp_ingest_patient_document`(
  in_case_id STRING,
  in_gcs_uri STRING
)
BEGIN
  -- Step A: Parse with layout-v1 (extract HTML tables & strip page_header/page_footer boilerplate)
  INSERT INTO `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.patient_case_parsed_docs` (
    case_id, gcs_uri, parsed_layout_v1, extracted_tables, clean_text_without_boilerplate, parsed_at
  )
  WITH parsed AS (
    SELECT
      AI.PARSE_DOCUMENT(
        in_gcs_uri,
        connection_id => '${BQ_CONNECTION_ID}',
        options => JSON '{"schema": "layout-v1"}'
      ) AS doc_json
  ),
  blocks AS (
    SELECT
      doc_json,
      block,
      SAFE_CAST(JSON_VALUE(block, '$.page_number') AS INT64) AS page_num,
      JSON_VALUE(block, '$.type') AS block_type,
      COALESCE(JSON_VALUE(block, '$.text'), JSON_VALUE(block, '$.html'), '') AS block_content
    FROM parsed,
    UNNEST(JSON_QUERY_ARRAY(doc_json, '$.blocks')) AS block
  )
  SELECT
    in_case_id AS case_id,
    in_gcs_uri AS gcs_uri,
    ANY_VALUE(doc_json) AS parsed_layout_v1,
    ARRAY_AGG(
      STRUCT(page_num AS page_number, block_content AS html_table)
      ORDER BY page_num
    ) AS extracted_tables,
    STRING_AGG(
      CONCAT('[Page ', CAST(COALESCE(page_num, 1) AS STRING), '] ', block_content),
      '\n'
      ORDER BY page_num
    ) AS clean_text_without_boilerplate,
    CURRENT_TIMESTAMP() AS parsed_at
  FROM blocks
  WHERE COALESCE(block_type, '') NOT IN ('page_header', 'page_footer');

  -- Step B: Layout-aware semantic chunking with AI.CHUNK_DOCUMENT (1000-token chunks)
  INSERT INTO `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.patient_case_chunks` (
    case_id, gcs_uri, chunk_id, start_page, end_page, content, chunked_at
  )
  SELECT
    in_case_id AS case_id,
    in_gcs_uri AS gcs_uri,
    chunk_id,
    start_page,
    end_page,
    content,
    CURRENT_TIMESTAMP() AS chunked_at
  FROM AI.CHUNK_DOCUMENT(
    (
      SELECT
        AI.PARSE_DOCUMENT(
          in_gcs_uri,
          connection_id => '${BQ_CONNECTION_ID}',
          options => JSON '{"schema": "raw-docai"}'
        ) AS parsed_document
    ),
    connection_id => '${BQ_CONNECTION_ID}',
    chunk_size => 1000
  );
END;
