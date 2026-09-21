// Package store is the local persistence layer for scenes, versions and themes.
//
// ADR-003: SQLite is the system of record for metadata and history; canonical scene
// and theme documents are also written to disk as JSON files so designs stay
// git-friendly and portable.
package store

import (
	"database/sql"
	_ "embed"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/google/uuid"
	_ "modernc.org/sqlite"
)

//go:embed schema.sql
var schemaSQL string

// Store owns the database handle and the scenes directory.
type Store struct {
	db        *sql.DB
	scenesDir string
}

// Options configures a Store. Zero values fall back to environment variables and
// then to local defaults, matching the spec's SQLITE_DB_PATH / SCENES_DIR.
type Options struct {
	DBPath    string
	ScenesDir string
}

func (o Options) resolve() Options {
	if o.DBPath == "" {
		o.DBPath = envOr("SQLITE_DB_PATH", "./data/isoforge.db")
	}
	if o.ScenesDir == "" {
		o.ScenesDir = envOr("SCENES_DIR", "./data/scenes")
	}
	return o
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// Open creates or opens the local database and applies the schema.
func Open(opts Options) (*Store, error) {
	opts = opts.resolve()

	if dir := filepath.Dir(opts.DBPath); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return nil, fmt.Errorf("create db directory: %w", err)
		}
	}
	if err := os.MkdirAll(opts.ScenesDir, 0o755); err != nil {
		return nil, fmt.Errorf("create scenes directory: %w", err)
	}

	// _time_format=sqlite keeps timestamps as readable text rather than driver-specific
	// encodings, which matters because these files are meant to be inspectable.
	dsn := opts.DBPath + "?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("open sqlite: %w", err)
	}
	// Single-writer local tool: more connections invite SQLITE_BUSY for no gain.
	db.SetMaxOpenConns(1)

	if _, err := db.Exec(schemaSQL); err != nil {
		db.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}

	s := &Store{db: db, scenesDir: opts.ScenesDir}
	if err := s.seedBuiltinThemes(); err != nil {
		db.Close()
		return nil, fmt.Errorf("seed builtin themes: %w", err)
	}
	return s, nil
}

// Close releases the database handle.
func (s *Store) Close() error { return s.db.Close() }

// DB exposes the handle for tests and health checks.
func (s *Store) DB() *sql.DB { return s.db }

// ScenesDir is where canonical scene/theme JSON files are written.
func (s *Store) ScenesDir() string { return s.scenesDir }

// Ping verifies the database is reachable.
func (s *Store) Ping() error { return s.db.Ping() }

func newID() string { return uuid.NewString() }

// nowRFC3339 formats the current time as sortable UTC text.
func nowRFC3339() string { return time.Now().UTC().Format(time.RFC3339) }

func toJSON(v any) (string, error) {
	data, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func fromJSON[T any](text string) (T, error) {
	var out T
	if text == "" {
		return out, nil
	}
	err := json.Unmarshal([]byte(text), &out)
	return out, err
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}
