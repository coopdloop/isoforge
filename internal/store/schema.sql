-- IsoForge local persistence schema.
--
-- The product spec expresses this in Postgres types (UUID, TIMESTAMPTZ, JSONB).
-- SQLite is the ratified target (ADR-003: zero-setup, single-writer, local), so the
-- mapping is made explicit here rather than being silently reinterpreted:
--
--   UUID        -> TEXT, UUIDv4 generated in Go
--   TIMESTAMPTZ -> TEXT, RFC3339 UTC ("2026-09-20T18:14:40Z"), lexically sortable
--   JSONB       -> TEXT with a json_valid() CHECK constraint
--   BOOLEAN     -> INTEGER 0/1
--
-- Canonical scene and theme JSON also lands on disk as files (see SCENES_DIR); this
-- database is the index and history, not the only copy.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    description              TEXT,
    -- Head pointer. Deliberately not a hard FK: scene_versions references projects,
    -- and a circular FK pair would make insert ordering painful for no real benefit.
    current_scene_version_id TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects (created_at);

CREATE TABLE IF NOT EXISTS scene_versions (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    conversation_id   TEXT,
    parent_version_id TEXT REFERENCES scene_versions (id) ON DELETE SET NULL,
    version_number    INTEGER NOT NULL,
    scene_json        TEXT NOT NULL CHECK (json_valid(scene_json)),
    patch_json        TEXT CHECK (patch_json IS NULL OR json_valid(patch_json)),
    scene_hash        TEXT NOT NULL,
    change_summary    TEXT,
    created_by        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scene_versions_project_id ON scene_versions (project_id);
-- Version numbers are dense and unique per project; this both enforces that and
-- serves the history listing.
CREATE UNIQUE INDEX IF NOT EXISTS idx_scene_versions_project_version
    ON scene_versions (project_id, version_number);

CREATE TABLE IF NOT EXISTS conversations (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    title          TEXT,
    model_provider TEXT,
    model_name     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_project_id ON conversations (project_id);

CREATE TABLE IF NOT EXISTS messages (
    id                        TEXT PRIMARY KEY,
    conversation_id           TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role                      TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content                   TEXT,
    tool_call_payload         TEXT CHECK (tool_call_payload IS NULL OR json_valid(tool_call_payload)),
    resulting_scene_version_id TEXT REFERENCES scene_versions (id) ON DELETE SET NULL,
    sequence_number           INTEGER NOT NULL,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages (conversation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_conversation_seq
    ON messages (conversation_id, sequence_number);

CREATE TABLE IF NOT EXISTS themes (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    is_builtin        INTEGER NOT NULL DEFAULT 0 CHECK (is_builtin IN (0, 1)),
    theme_json        TEXT NOT NULL CHECK (json_valid(theme_json)),
    source_project_id TEXT REFERENCES projects (id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- Names are the user-facing handle for `isoforge theme apply <name>`, so they must
-- be unambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS idx_themes_name ON themes (name);

CREATE TABLE IF NOT EXISTS render_jobs (
    id               TEXT PRIMARY KEY,
    scene_version_id TEXT REFERENCES scene_versions (id) ON DELETE CASCADE,
    job_type         TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    error_message    TEXT,
    requested_by     TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_render_jobs_scene_version ON render_jobs (scene_version_id);

CREATE TABLE IF NOT EXISTS export_artifacts (
    id                     TEXT PRIMARY KEY,
    render_job_id          TEXT REFERENCES render_jobs (id) ON DELETE CASCADE,
    artifact_type          TEXT NOT NULL,
    file_path              TEXT NOT NULL,
    width                  INTEGER,
    height                 INTEGER,
    transparent_background INTEGER NOT NULL DEFAULT 1 CHECK (transparent_background IN (0, 1)),
    metadata_json          TEXT CHECK (metadata_json IS NULL OR json_valid(metadata_json)),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_export_artifacts_job ON export_artifacts (render_job_id);

CREATE TABLE IF NOT EXISTS api_sessions (
    id            TEXT PRIMARY KEY,
    project_id    TEXT REFERENCES projects (id) ON DELETE CASCADE,
    -- Retained for session bookkeeping only; there is no auth (localhost, single user).
    session_token TEXT,
    client_type   TEXT,
    last_seen_at  TEXT,
    expires_at    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_sessions_project ON api_sessions (project_id);

CREATE TABLE IF NOT EXISTS websocket_events (
    id               TEXT PRIMARY KEY,
    session_id       TEXT REFERENCES api_sessions (id) ON DELETE CASCADE,
    scene_version_id TEXT REFERENCES scene_versions (id) ON DELETE SET NULL,
    event_type       TEXT NOT NULL,
    payload_json     TEXT CHECK (payload_json IS NULL OR json_valid(payload_json)),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_websocket_events_session ON websocket_events (session_id);
