package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// ThemeDoc is the portable theme format written to disk and shared between users.
type ThemeDoc struct {
	Name        string            `json:"name"`
	Description string            `json:"description,omitempty"`
	Colors      map[string]string `json:"colors"`
}

// builtinThemes are seeded on first boot and match the curated palettes the agent
// suggests, so "use the nord palette" resolves to the same colors everywhere.
var builtinThemes = []ThemeDoc{
	{
		Name:        "violet-dev",
		Description: "Default IsoForge palette: electric violet with a cyan accent.",
		Colors:      map[string]string{"base": "#7C5CFF", "accent": "#22D3EE", "ink": "#0B0E14"},
	},
	{
		Name:        "ember",
		Description: "Hot forge tones for build and CI tooling.",
		Colors:      map[string]string{"hot": "#FF6B35", "warm": "#F7B801", "cool": "#2EC4B6", "ink": "#1A1423"},
	},
	{
		Name:        "forest",
		Description: "Calm greens for data and infrastructure tools.",
		Colors:      map[string]string{"leaf": "#34D399", "moss": "#059669", "bark": "#78350F", "ink": "#022C22"},
	},
	{
		Name:        "nord",
		Description: "Muted arctic palette, popular in developer themes.",
		Colors:      map[string]string{"polar": "#2E3440", "frost": "#88C0D0", "aurora": "#A3BE8C", "ink": "#242933"},
	},
	{
		Name:        "mono",
		Description: "Neutral greyscale for logos that must work anywhere.",
		Colors:      map[string]string{"light": "#E2E8F0", "mid": "#94A3B8", "dark": "#475569", "ink": "#0F172A"},
	},
}

// seedBuiltinThemes inserts the curated palettes once. It is idempotent, so an
// upgrade that adds a theme picks it up without disturbing user edits.
func (s *Store) seedBuiltinThemes() error {
	for _, doc := range builtinThemes {
		encoded, err := json.Marshal(doc)
		if err != nil {
			return err
		}
		now := nowRFC3339()
		_, err = s.db.Exec(
			`INSERT INTO themes (id, name, is_builtin, theme_json, created_at, updated_at)
			 VALUES (?, ?, 1, ?, ?, ?)
			 ON CONFLICT (name) DO NOTHING`,
			newID(), doc.Name, string(encoded), now, now,
		)
		if err != nil {
			return err
		}
	}
	return nil
}

// SaveTheme creates or replaces a user theme.
func (s *Store) SaveTheme(name string, doc ThemeDoc, sourceProjectID string) (*Theme, error) {
	name = strings.TrimSpace(name)
	if name == "" {
		return nil, fmt.Errorf("theme name is required")
	}
	if len(doc.Colors) == 0 {
		return nil, fmt.Errorf("theme must define at least one color")
	}
	doc.Name = name

	// Builtins are read-only so a user cannot shadow a palette the agent references.
	var isBuiltin int
	err := s.db.QueryRow(`SELECT is_builtin FROM themes WHERE name = ?`, name).Scan(&isBuiltin)
	if err == nil && isBuiltin == 1 {
		return nil, fmt.Errorf("'%s' is a builtin theme and cannot be overwritten", name)
	}
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}

	encoded, err := json.Marshal(doc)
	if err != nil {
		return nil, err
	}
	now := nowRFC3339()

	_, err = s.db.Exec(
		`INSERT INTO themes (id, name, is_builtin, theme_json, source_project_id, created_at, updated_at)
		 VALUES (?, ?, 0, ?, ?, ?, ?)
		 ON CONFLICT (name) DO UPDATE SET
		   theme_json = excluded.theme_json,
		   source_project_id = excluded.source_project_id,
		   updated_at = excluded.updated_at`,
		newID(), name, string(encoded), nullable(sourceProjectID), now, now,
	)
	if err != nil {
		return nil, fmt.Errorf("save theme: %w", err)
	}

	_ = s.writeThemeFile(name, encoded)
	return s.GetThemeByName(name)
}

func (s *Store) writeThemeFile(name string, encoded []byte) error {
	dir := filepath.Join(s.scenesDir, "themes")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	var pretty json.RawMessage = encoded
	indented, err := json.MarshalIndent(pretty, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, name+".theme.json"), append(indented, '\n'), 0o644)
}

const themeSelect = `
	SELECT id, name, is_builtin, theme_json, COALESCE(source_project_id, ''),
	       created_at, updated_at
	FROM themes`

func scanTheme(row interface{ Scan(...any) error }) (*Theme, error) {
	var t Theme
	var builtin int
	var themeText string
	err := row.Scan(&t.ID, &t.Name, &builtin, &themeText, &t.SourceProjectID,
		&t.CreatedAt, &t.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	t.IsBuiltin = builtin == 1
	t.Theme = json.RawMessage(themeText)
	return &t, nil
}

// GetTheme fetches a theme by id.
func (s *Store) GetTheme(id string) (*Theme, error) {
	return scanTheme(s.db.QueryRow(themeSelect+` WHERE id = ?`, id))
}

// GetThemeByName fetches a theme by its unique name.
func (s *Store) GetThemeByName(name string) (*Theme, error) {
	return scanTheme(s.db.QueryRow(themeSelect+` WHERE name = ?`, name))
}

// ListThemes returns builtins first, then user themes, each alphabetically.
func (s *Store) ListThemes() ([]*Theme, error) {
	rows, err := s.db.Query(themeSelect + ` ORDER BY is_builtin DESC, name ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []*Theme{}
	for rows.Next() {
		t, err := scanTheme(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

// DeleteTheme removes a user theme. Builtins are protected.
func (s *Store) DeleteTheme(id string) error {
	var builtin int
	err := s.db.QueryRow(`SELECT is_builtin FROM themes WHERE id = ?`, id).Scan(&builtin)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if builtin == 1 {
		return fmt.Errorf("builtin themes cannot be deleted")
	}
	_, err = s.db.Exec(`DELETE FROM themes WHERE id = ?`, id)
	return err
}

// ImportTheme ingests a portable theme document.
func (s *Store) ImportTheme(data []byte) (*Theme, error) {
	var doc ThemeDoc
	if err := json.Unmarshal(data, &doc); err != nil {
		return nil, fmt.Errorf("invalid theme JSON: %w", err)
	}
	if doc.Name == "" {
		return nil, fmt.Errorf("theme document must have a name")
	}
	// Imported themes never overwrite a builtin; they are suffixed instead.
	name := doc.Name
	if existing, err := s.GetThemeByName(name); err == nil && existing.IsBuiltin {
		name = name + "-imported"
	}
	return s.SaveTheme(name, doc, "")
}
