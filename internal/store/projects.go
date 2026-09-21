package store

import (
	"database/sql"
	"errors"
	"fmt"
)

// ErrNotFound is returned when a requested row does not exist.
var ErrNotFound = errors.New("not found")

// CreateProject inserts a new workspace.
func (s *Store) CreateProject(name, description string) (*Project, error) {
	if name == "" {
		return nil, fmt.Errorf("project name is required")
	}
	now := nowRFC3339()
	p := &Project{ID: newID(), Name: name, Description: description, CreatedAt: now, UpdatedAt: now}

	_, err := s.db.Exec(
		`INSERT INTO projects (id, name, description, created_at, updated_at)
		 VALUES (?, ?, ?, ?, ?)`,
		p.ID, p.Name, nullable(p.Description), p.CreatedAt, p.UpdatedAt,
	)
	if err != nil {
		return nil, fmt.Errorf("create project: %w", err)
	}
	return p, nil
}

const projectSelect = `
	SELECT p.id, p.name, COALESCE(p.description, ''), COALESCE(p.current_scene_version_id, ''),
	       p.created_at, p.updated_at,
	       COALESCE((SELECT version_number FROM scene_versions WHERE id = p.current_scene_version_id), 0),
	       (SELECT COUNT(*) FROM scene_versions WHERE project_id = p.id)
	FROM projects p`

func scanProject(row interface{ Scan(...any) error }) (*Project, error) {
	var p Project
	err := row.Scan(&p.ID, &p.Name, &p.Description, &p.CurrentSceneVersionID,
		&p.CreatedAt, &p.UpdatedAt, &p.CurrentVersionNumber, &p.VersionCount)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &p, nil
}

// GetProject fetches one project by id.
func (s *Store) GetProject(id string) (*Project, error) {
	return scanProject(s.db.QueryRow(projectSelect+` WHERE p.id = ?`, id))
}

// ListProjects returns projects newest first.
func (s *Store) ListProjects(limit int) ([]*Project, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := s.db.Query(projectSelect+` ORDER BY p.created_at DESC, p.id LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []*Project{}
	for rows.Next() {
		p, err := scanProject(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, p)
	}
	return out, rows.Err()
}

// DeleteProject removes a project and, by cascade, its versions and conversations.
func (s *Store) DeleteProject(id string) error {
	result, err := s.db.Exec(`DELETE FROM projects WHERE id = ?`, id)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return ErrNotFound
	}
	// Scene files are left on disk deliberately: this is a local design tool and an
	// accidental project delete should not destroy exported artwork.
	return nil
}

// UpdateProject renames or re-describes a project.
func (s *Store) UpdateProject(id, name, description string) (*Project, error) {
	result, err := s.db.Exec(
		`UPDATE projects
		 SET name = COALESCE(NULLIF(?, ''), name),
		     description = COALESCE(NULLIF(?, ''), description),
		     updated_at = ?
		 WHERE id = ?`,
		name, description, nowRFC3339(), id,
	)
	if err != nil {
		return nil, err
	}
	if affected, _ := result.RowsAffected(); affected == 0 {
		return nil, ErrNotFound
	}
	return s.GetProject(id)
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}
