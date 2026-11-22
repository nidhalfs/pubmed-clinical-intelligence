-- ============================================================================
-- 06_private_pdfs_rag.sql
-- Optional Customer Private PDF Knowledge Base (Institutional Protocols,
-- Unpublished Case Series, Local Guidelines) using:
--   AI.CHUNK_DOCUMENT + Autonomous AI.EMBED + AI.SEARCH
--
-- Customers simply drop PDFs into `gs://${PRIVATE_PDFS_BUCKET}/` and run this
-- incremental ingestion procedure — zero external RAG infrastructure required.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.private_medical_chunks` (
  uri STRING NOT NULL,
  chunk_id INT64 NOT NULL,
  start_page INT64,
  end_page INT64,
  content STRING,
  embedding STRUCT<result ARRAY<FLOAT64>, status STRING>
    GENERATED ALWAYS AS (
      AI.EMBED(
        content,
        connection_id => '${BQ_CONNECTION_ID}',
        endpoint => 'text-embedding-005'
      )
    ) STORED OPTIONS (asynchronous = TRUE),
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY uri;

CREATE SEARCH INDEX IF NOT EXISTS idx_private_pdfs_lexical
ON `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.private_medical_chunks`(content);

-- Procedure to incrementally chunk & ingest any new PDFs uploaded to the customer's private bucket
CREATE OR REPLACE PROCEDURE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.sp_sync_private_pdfs`()
BEGIN
  MERGE `${GCP_PROJECT_ID}.${BQ_DATASET_ID}.private_medical_chunks` T
  USING (
    SELECT
      uri,
      chunk_id,
      start_page,
      end_page,
      content
    FROM AI.CHUNK_DOCUMENT(
      'gs://${PRIVATE_PDFS_BUCKET}/*.pdf',
      connection_id => '${BQ_CONNECTION_ID}',
      chunk_size => 1000
    )
    WHERE content IS NOT NULL AND LENGTH(TRIM(content)) > 20
  ) S
  ON T.uri = S.uri AND T.chunk_id = S.chunk_id
  WHEN NOT MATCHED THEN
    INSERT (uri, chunk_id, start_page, end_page, content, ingested_at)
    VALUES (S.uri, S.chunk_id, S.start_page, S.end_page, S.content, CURRENT_TIMESTAMP());
END;
