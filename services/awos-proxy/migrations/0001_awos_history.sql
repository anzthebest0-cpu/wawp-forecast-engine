CREATE TABLE IF NOT EXISTS awos_samples (
  observed_ms INTEGER PRIMARY KEY,
  retrieved_ms INTEGER NOT NULL,
  payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE TABLE IF NOT EXISTS awos_reports (
  observed_ms INTEGER NOT NULL,
  report_text TEXT NOT NULL,
  report_date_utc TEXT NOT NULL,
  PRIMARY KEY (observed_ms, report_text)
);
