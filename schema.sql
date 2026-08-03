CREATE SEQUENCE IF NOT EXISTS batch_seq;
CREATE SEQUENCE IF NOT EXISTS event_seq;

CREATE TABLE IF NOT EXISTS batches (
  batch_id      BIGINT DEFAULT nextval('batch_seq'),
  file_name     VARCHAR,
  source_system VARCHAR,
  profile_name  VARCHAR,
  structure     VARCHAR,
  match_ratio   DOUBLE,
  row_count     INTEGER,
  source_path   VARCHAR,    
  ingested_at   TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
  event_id   BIGINT DEFAULT nextval('event_seq'),
  batch_id   BIGINT,
  line_no    INTEGER,
  ts_utc     TIMESTAMP,
  ts_raw     VARCHAR,
  level      VARCHAR,
  category   VARCHAR,
  component  VARCHAR,
  message    VARCHAR,
  raw        VARCHAR,
  attributes JSON
);