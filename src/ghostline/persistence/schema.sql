-- GhostLine SQLite schema
-- Keep column names lowercase_snake_case so repository.py can map via dataclass.

CREATE TABLE IF NOT EXISTS calls (
    call_sid          TEXT PRIMARY KEY,
    start_time        TEXT,
    end_time          TEXT NULL,
    voice_id          TEXT,
    campaign          TEXT,
    persona           TEXT,
    phone_number      TEXT,
    outcome           TEXT NULL,
    conversion_score  REAL NULL,
    notes             TEXT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid          TEXT,
    role              TEXT,
    content           TEXT,
    timestamp         TEXT,
    sales_stage       TEXT NULL,
    sentiment_score   REAL NULL,
    interest_level    REAL NULL,
    objection_type    TEXT NULL,
    trigger_used      TEXT NULL,
    FOREIGN KEY (call_sid) REFERENCES calls(call_sid)
);

CREATE INDEX IF NOT EXISTS idx_messages_call_sid ON messages(call_sid);

CREATE TABLE IF NOT EXISTS objections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid        TEXT,
    objection_text  TEXT,
    objection_type  TEXT,
    response_used   TEXT,
    resolved        BOOLEAN,
    timestamp       TEXT,
    FOREIGN KEY (call_sid) REFERENCES calls(call_sid)
);

CREATE TABLE IF NOT EXISTS customer_profiles (
    phone_number        TEXT PRIMARY KEY,
    communication_style TEXT NULL,
    pain_points         TEXT NULL,
    response_rates      TEXT NULL,
    preferred_persona   TEXT NULL,
    last_updated        TEXT
);
