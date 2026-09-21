package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/coopdloop/isoforge/internal/isodsl"
)

// SaveSceneOptions describes one new immutable version.
type SaveSceneOptions struct {
	ProjectID      string
	Scene          any
	Patch          any
	ConversationID string
	ChangeSummary  string
	CreatedBy      string
}

// SaveScene validates a scene and appends it as a new version.
//
// Nothing is ever updated in place (ADR-001). The project's head pointer moves to the
// new row, so history, diff and revert are all just queries over an append-only log.
func (s *Store) SaveScene(opts SaveSceneOptions) (*SceneVersion, error) {
	if opts.ProjectID == "" {
		return nil, fmt.Errorf("project_id is required")
	}

	// Enforce schema validity on write, per the scene_store responsibilities. An
	// invalid document must never reach storage, whatever wrote it.
	if result := isodsl.Validate(opts.Scene); !result.Valid {
		return nil, &isodsl.ValidationError{Result: result}
	}

	canonical, err := isodsl.CanonicalJSON(opts.Scene, -1)
	if err != nil {
		return nil, fmt.Errorf("canonicalise scene: %w", err)
	}
	hash, err := isodsl.SceneHash(opts.Scene)
	if err != nil {
		return nil, err
	}

	tx, err := s.db.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()

	var parentID sql.NullString
	var nextVersion int
	err = tx.QueryRow(
		`SELECT COALESCE(MAX(version_number), 0) + 1 FROM scene_versions WHERE project_id = ?`,
		opts.ProjectID,
	).Scan(&nextVersion)
	if err != nil {
		return nil, err
	}
	if err := tx.QueryRow(
		`SELECT current_scene_version_id FROM projects WHERE id = ?`, opts.ProjectID,
	).Scan(&parentID); errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	} else if err != nil {
		return nil, err
	}

	var patchText any
	if opts.Patch != nil {
		encoded, err := json.Marshal(opts.Patch)
		if err != nil {
			return nil, fmt.Errorf("encode patch: %w", err)
		}
		patchText = string(encoded)
	}

	now := nowRFC3339()
	version := &SceneVersion{
		ID:              newID(),
		ProjectID:       opts.ProjectID,
		ConversationID:  opts.ConversationID,
		ParentVersionID: parentID.String,
		VersionNumber:   nextVersion,
		Scene:           json.RawMessage(canonical),
		SceneHash:       hash,
		ChangeSummary:   opts.ChangeSummary,
		CreatedBy:       opts.CreatedBy,
		CreatedAt:       now,
		UpdatedAt:       now,
	}
	if patchText != nil {
		version.Patch = json.RawMessage(patchText.(string))
	}

	_, err = tx.Exec(
		`INSERT INTO scene_versions
		   (id, project_id, conversation_id, parent_version_id, version_number,
		    scene_json, patch_json, scene_hash, change_summary, created_by,
		    created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		version.ID, version.ProjectID, nullable(version.ConversationID),
		nullableNS(parentID), version.VersionNumber, string(canonical), patchText,
		hash, nullable(version.ChangeSummary), nullable(version.CreatedBy), now, now,
	)
	if err != nil {
		return nil, fmt.Errorf("insert scene version: %w", err)
	}

	if _, err := tx.Exec(
		`UPDATE projects SET current_scene_version_id = ?, updated_at = ? WHERE id = ?`,
		version.ID, now, opts.ProjectID,
	); err != nil {
		return nil, err
	}

	if err := tx.Commit(); err != nil {
		return nil, err
	}

	// Mirror the canonical document to disk so designs stay git-friendly and
	// portable (ADR-003). A write failure here is not fatal: the database is the
	// system of record and the file can be regenerated.
	_ = s.writeSceneFile(opts.ProjectID, version.VersionNumber, canonical)

	return version, nil
}

func (s *Store) writeSceneFile(projectID string, version int, canonical []byte) error {
	dir := filepath.Join(s.scenesDir, projectID)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	// Re-indent for human readability; the hash is computed from the compact form.
	var scene any
	if err := json.Unmarshal(canonical, &scene); err != nil {
		return err
	}
	pretty, err := isodsl.CanonicalJSON(scene, 2)
	if err != nil {
		return err
	}
	name := fmt.Sprintf("v%d.isoforge.json", version)
	if err := os.WriteFile(filepath.Join(dir, name), append(pretty, '\n'), 0o644); err != nil {
		return err
	}
	// `latest` gives tooling a stable path that always points at the head.
	return os.WriteFile(filepath.Join(dir, "latest.isoforge.json"), append(pretty, '\n'), 0o644)
}

func nullableNS(ns sql.NullString) any {
	if !ns.Valid || ns.String == "" {
		return nil
	}
	return ns.String
}

const versionSelect = `
	SELECT id, project_id, COALESCE(conversation_id, ''), COALESCE(parent_version_id, ''),
	       version_number, scene_json, COALESCE(patch_json, ''), scene_hash,
	       COALESCE(change_summary, ''), COALESCE(created_by, ''), created_at, updated_at
	FROM scene_versions`

func scanVersion(row interface{ Scan(...any) error }) (*SceneVersion, error) {
	var v SceneVersion
	var sceneText, patchText string
	err := row.Scan(&v.ID, &v.ProjectID, &v.ConversationID, &v.ParentVersionID,
		&v.VersionNumber, &sceneText, &patchText, &v.SceneHash,
		&v.ChangeSummary, &v.CreatedBy, &v.CreatedAt, &v.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	v.Scene = json.RawMessage(sceneText)
	if patchText != "" {
		v.Patch = json.RawMessage(patchText)
	}
	return &v, nil
}

// GetSceneVersion fetches one version by id.
func (s *Store) GetSceneVersion(id string) (*SceneVersion, error) {
	return scanVersion(s.db.QueryRow(versionSelect+` WHERE id = ?`, id))
}

// GetSceneVersionByNumber fetches version N of a project.
func (s *Store) GetSceneVersionByNumber(projectID string, number int) (*SceneVersion, error) {
	return scanVersion(s.db.QueryRow(
		versionSelect+` WHERE project_id = ? AND version_number = ?`, projectID, number))
}

// GetCurrentScene returns the project's head version.
func (s *Store) GetCurrentScene(projectID string) (*SceneVersion, error) {
	return scanVersion(s.db.QueryRow(
		versionSelect+` WHERE id = (SELECT current_scene_version_id FROM projects WHERE id = ?)`,
		projectID))
}

// History lists a project's versions newest first, without the scene payloads.
func (s *Store) History(projectID string, limit int) ([]HistoryEntry, error) {
	if limit <= 0 {
		limit = 200
	}
	rows, err := s.db.Query(
		`SELECT v.id, v.version_number, COALESCE(v.parent_version_id, ''),
		        COALESCE(v.conversation_id, ''), v.scene_hash,
		        COALESCE(v.change_summary, ''), COALESCE(v.created_by, ''), v.created_at,
		        (v.id = COALESCE((SELECT current_scene_version_id FROM projects WHERE id = ?), '')),
		        COALESCE(json_array_length(v.scene_json, '$.shapes'), 0)
		 FROM scene_versions v
		 WHERE v.project_id = ?
		 ORDER BY v.version_number DESC
		 LIMIT ?`,
		projectID, projectID, limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []HistoryEntry{}
	for rows.Next() {
		var e HistoryEntry
		if err := rows.Scan(&e.ID, &e.VersionNumber, &e.ParentVersionID, &e.ConversationID,
			&e.SceneHash, &e.ChangeSummary, &e.CreatedBy, &e.CreatedAt,
			&e.IsCurrent, &e.ShapeCount); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// Revert restores an earlier version by appending a copy of it as a new version.
//
// History is never truncated: reverting to v2 from v5 creates v6 with v2's content,
// so the user can always undo the undo. This is the behaviour the spec's "revert"
// implies but does not state, and destroying v3-v5 would be an unpleasant surprise
// in a conversational tool where reverts are cheap and frequent.
func (s *Store) Revert(projectID string, targetVersion int, createdBy string) (*SceneVersion, error) {
	target, err := s.GetSceneVersionByNumber(projectID, targetVersion)
	if err != nil {
		return nil, err
	}

	var scene any
	if err := json.Unmarshal(target.Scene, &scene); err != nil {
		return nil, fmt.Errorf("decode target scene: %w", err)
	}

	return s.SaveScene(SaveSceneOptions{
		ProjectID:     projectID,
		Scene:         scene,
		ChangeSummary: fmt.Sprintf("Revert to v%d", targetVersion),
		CreatedBy:     createdBy,
	})
}
