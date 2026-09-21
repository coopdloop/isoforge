package store

import "encoding/json"

// Project is a logical logo workspace.
type Project struct {
	ID                    string `json:"id"`
	Name                  string `json:"name"`
	Description           string `json:"description,omitempty"`
	CurrentSceneVersionID string `json:"current_scene_version_id,omitempty"`
	CurrentVersionNumber  int    `json:"current_version_number"`
	VersionCount          int    `json:"version_count"`
	CreatedAt             string `json:"created_at"`
	UpdatedAt             string `json:"updated_at"`
}

// SceneVersion is one immutable snapshot of a design.
//
// Every accepted mutation appends a new row (ADR-001). Nothing is ever updated in
// place, which is what makes history, diff and revert trivially correct.
type SceneVersion struct {
	ID              string          `json:"id"`
	ProjectID       string          `json:"project_id"`
	ConversationID  string          `json:"conversation_id,omitempty"`
	ParentVersionID string          `json:"parent_version_id,omitempty"`
	VersionNumber   int             `json:"version_number"`
	Scene           json.RawMessage `json:"scene"`
	Patch           json.RawMessage `json:"patch,omitempty"`
	SceneHash       string          `json:"scene_hash"`
	ChangeSummary   string          `json:"change_summary,omitempty"`
	CreatedBy       string          `json:"created_by,omitempty"`
	CreatedAt       string          `json:"created_at"`
	UpdatedAt       string          `json:"updated_at"`
}

// HistoryEntry is a lightweight version listing that omits the full scene payload,
// so rendering a long history does not ship megabytes of JSON.
type HistoryEntry struct {
	ID              string `json:"id"`
	VersionNumber   int    `json:"version_number"`
	ParentVersionID string `json:"parent_version_id,omitempty"`
	ConversationID  string `json:"conversation_id,omitempty"`
	SceneHash       string `json:"scene_hash"`
	ChangeSummary   string `json:"change_summary,omitempty"`
	CreatedBy       string `json:"created_by,omitempty"`
	CreatedAt       string `json:"created_at"`
	IsCurrent       bool   `json:"is_current"`
	ShapeCount      int    `json:"shape_count"`
}

// Theme is a reusable palette.
type Theme struct {
	ID              string          `json:"id"`
	Name            string          `json:"name"`
	IsBuiltin       bool            `json:"is_builtin"`
	Theme           json.RawMessage `json:"theme"`
	SourceProjectID string          `json:"source_project_id,omitempty"`
	CreatedAt       string          `json:"created_at"`
	UpdatedAt       string          `json:"updated_at"`
}

// Conversation links a chat session to a project.
type Conversation struct {
	ID            string `json:"id"`
	ProjectID     string `json:"project_id"`
	Title         string `json:"title,omitempty"`
	ModelProvider string `json:"model_provider,omitempty"`
	ModelName     string `json:"model_name,omitempty"`
	CreatedAt     string `json:"created_at"`
	UpdatedAt     string `json:"updated_at"`
}

// Message is one durable chat turn.
type Message struct {
	ID                      string          `json:"id"`
	ConversationID          string          `json:"conversation_id"`
	Role                    string          `json:"role"`
	Content                 string          `json:"content,omitempty"`
	ToolCallPayload         json.RawMessage `json:"tool_call_payload,omitempty"`
	ResultingSceneVersionID string          `json:"resulting_scene_version_id,omitempty"`
	SequenceNumber          int             `json:"sequence_number"`
	CreatedAt               string          `json:"created_at"`
	UpdatedAt               string          `json:"updated_at"`
}

// DiffResult compares two versions of a scene.
type DiffResult struct {
	FromVersion int             `json:"from_version"`
	ToVersion   int             `json:"to_version"`
	FromHash    string          `json:"from_hash"`
	ToHash      string          `json:"to_hash"`
	Changed     bool            `json:"changed"`
	Patch       json.RawMessage `json:"patch"`
	Summary     DiffSummary     `json:"summary"`
}

// DiffSummary is a human-readable count of what changed, for CLI and UI headers.
type DiffSummary struct {
	Added    int      `json:"added"`
	Removed  int      `json:"removed"`
	Replaced int      `json:"replaced"`
	Moved    int      `json:"moved"`
	Paths    []string `json:"paths"`
}
