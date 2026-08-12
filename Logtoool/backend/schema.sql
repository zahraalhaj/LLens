-- SQLite Schema for AI-Powered Log Visualization Software
-- Enable WAL mode
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    total_events INTEGER DEFAULT 0,
    matched_profile TEXT,
    match_ratio REAL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    line_no INTEGER NOT NULL,
    ts_utc TEXT,
    ts_raw TEXT NOT NULL,
    ts_confidence TEXT NOT NULL, -- parsed, assumed_utc, assumed_local, unparseable
    level TEXT NOT NULL,         -- DEBUG, INFO, WARN, ERROR, CRITICAL, UNKNOWN
    source_system TEXT NOT NULL,
    component TEXT,
    message TEXT NOT NULL,
    raw TEXT NOT NULL,
    attributes TEXT,             -- JSON string
    FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_ts_utc ON events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_events_level ON events(level);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_system);
CREATE INDEX IF NOT EXISTS idx_events_component ON events(component);
CREATE INDEX IF NOT EXISTS idx_events_batch_id ON events(batch_id);
CREATE INDEX IF NOT EXISTS idx_events_file_name ON events(file_name);

-- Auth: simple built-in username+password accounts (~20 internal users).
-- Passwords are bcrypt hashes, never plaintext. "role" is intentionally just
-- two tiers (admin/member) -- enough to gate destructive actions (clearing
-- data, managing profiles/alert rules) without building a full RBAC system
-- nobody at this scale needs yet.
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member', -- 'admin' or 'member'
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- Server-side sessions (not JWT) so a session can be revoked immediately --
-- e.g. an admin disabling a departing employee's account should end their
-- session right away, not whenever a stateless token happens to expire.
-- token_hash stores sha256(raw token); the raw token itself only ever lives
-- in the user's browser cookie, never at rest in the database.
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
