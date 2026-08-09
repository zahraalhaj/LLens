CREATE SEQUENCE IF NOT EXISTS batch_seq;
CREATE SEQUENCE IF NOT EXISTS event_seq;

CREATE TABLE IF NOT EXISTS batches (
  batch_id      BIGINT DEFAULT nextval('batch_seq') PRIMARY KEY,
  file_name     VARCHAR NOT NULL,
  source_system VARCHAR,
  profile_name  VARCHAR,
  structure     VARCHAR,
  match_ratio   DOUBLE,
  row_count     INTEGER,
  source_path   VARCHAR,
  ingested_at   TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS events (
  event_id   BIGINT DEFAULT nextval('event_seq') PRIMARY KEY,
  batch_id   BIGINT NOT NULL REFERENCES batches(batch_id),
  line_no    INTEGER,
  ts_utc     TIMESTAMP,
  ts_raw     VARCHAR,
  level      VARCHAR NOT NULL,
  category   VARCHAR,
  component  VARCHAR,
  message    VARCHAR,
  raw        VARCHAR,
  attributes JSON
);

CREATE INDEX IF NOT EXISTS idx_events_ts    ON events (ts_utc);
CREATE INDEX IF NOT EXISTS idx_events_level ON events (level);
CREATE INDEX IF NOT EXISTS idx_events_batch ON events (batch_id, ts_utc);
