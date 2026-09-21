package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Backend talks to the Python service hosting the agent and render engines.
//
// ADR-002 requires the gateway to survive backend crashes and report them clearly
// rather than propagating opaque 502s, so every call here converts transport failures
// into a typed BackendError the handlers can present usefully.
type Backend struct {
	AgentURL  string
	RenderURL string
	client    *http.Client
}

// NewBackend creates a client with a generous timeout, since LLM turns are slow.
func NewBackend(agentURL, renderURL string) *Backend {
	return &Backend{
		AgentURL:  agentURL,
		RenderURL: renderURL,
		client:    &http.Client{Timeout: 5 * time.Minute},
	}
}

// BackendError carries a failed upstream call.
type BackendError struct {
	Service    string
	StatusCode int
	Body       json.RawMessage
	Err        error
}

func (e *BackendError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("%s service unreachable: %v", e.Service, e.Err)
	}
	return fmt.Sprintf("%s service returned %d: %s", e.Service, e.StatusCode, string(e.Body))
}

func (e *BackendError) Unwrap() error { return e.Err }

// Unavailable reports whether the service could not be reached at all, which the
// CLI presents differently from a normal rejection.
func (e *BackendError) Unavailable() bool { return e.Err != nil }

func (b *Backend) do(ctx context.Context, service, method, url string, body any, out any) error {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("encode request: %w", err)
		}
		reader = bytes.NewReader(encoded)
	}

	req, err := http.NewRequestWithContext(ctx, method, url, reader)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := b.client.Do(req)
	if err != nil {
		return &BackendError{Service: service, Err: err}
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return &BackendError{Service: service, Err: err}
	}

	if resp.StatusCode >= 400 {
		return &BackendError{Service: service, StatusCode: resp.StatusCode, Body: data}
	}
	if out != nil && len(data) > 0 {
		if err := json.Unmarshal(data, out); err != nil {
			return fmt.Errorf("decode %s response: %w", service, err)
		}
	}
	return nil
}

// Health checks one backend service.
func (b *Backend) Health(ctx context.Context) (map[string]any, error) {
	var out map[string]any
	// A short timeout: health is used to gate startup, so it must fail fast.
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	err := b.do(ctx, "agent", http.MethodGet, b.AgentURL+"/health", nil, &out)
	return out, err
}

// TurnRequest asks the agent to process one chat message.
type TurnRequest struct {
	Message          string  `json:"message"`
	ModelOverride    string  `json:"model_override,omitempty"`
	ProviderOverride string  `json:"provider_override,omitempty"`
	Temperature      float64 `json:"temperature,omitempty"`
}

// TurnResponse is the agent's validated result.
type TurnResponse struct {
	Reply          string            `json:"reply"`
	Scene          json.RawMessage   `json:"scene"`
	Patch          []json.RawMessage `json:"patch"`
	IsFullScene    bool              `json:"is_full_scene"`
	Summary        string            `json:"summary"`
	SceneHash      string            `json:"scene_hash"`
	Changed        bool              `json:"changed"`
	RepairAttempts int               `json:"repair_attempts"`
	ThemeSaved     *struct {
		Name   string            `json:"name"`
		Colors map[string]string `json:"colors"`
	} `json:"theme_saved"`
	// Providers disagree about usage payloads: OpenRouter mixes in booleans and
	// nested objects alongside token counts, so this stays deliberately untyped and
	// is passed through for display rather than interpreted.
	Usage map[string]any `json:"usage"`
}

// CreateConversation starts an agent conversation, optionally seeded with a scene.
func (b *Backend) CreateConversation(ctx context.Context, projectID, title string, scene json.RawMessage) (string, error) {
	body := map[string]any{"project_id": projectID, "title": title}
	if len(scene) > 0 {
		body["scene"] = scene
	}
	var out struct {
		ID string `json:"id"`
	}
	if err := b.do(ctx, "agent", http.MethodPost, b.AgentURL+"/conversations", body, &out); err != nil {
		return "", err
	}
	return out.ID, nil
}

// RunTurn processes one chat turn.
func (b *Backend) RunTurn(ctx context.Context, conversationID string, req TurnRequest) (*TurnResponse, error) {
	var out TurnResponse
	url := fmt.Sprintf("%s/conversations/%s/turns", b.AgentURL, conversationID)
	if err := b.do(ctx, "agent", http.MethodPost, url, req, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ReloadScene points an existing conversation at a different scene.
func (b *Backend) ReloadScene(ctx context.Context, conversationID string, scene json.RawMessage) error {
	url := fmt.Sprintf("%s/conversations/%s/reload", b.AgentURL, conversationID)
	return b.do(ctx, "agent", http.MethodPost, url, map[string]any{"scene": scene}, nil)
}

// SetThemeLock freezes or unfreezes the palette.
func (b *Backend) SetThemeLock(ctx context.Context, conversationID string, locked bool) error {
	url := fmt.Sprintf("%s/conversations/%s/theme-lock", b.AgentURL, conversationID)
	return b.do(ctx, "agent", http.MethodPost, url, map[string]any{"locked": locked}, nil)
}

// DeleteConversation ends an agent conversation.
func (b *Backend) DeleteConversation(ctx context.Context, conversationID string) error {
	url := fmt.Sprintf("%s/conversations/%s", b.AgentURL, conversationID)
	return b.do(ctx, "agent", http.MethodDelete, url, nil, nil)
}

// RenderSVG renders a scene for preview or CLI display.
func (b *Backend) RenderSVG(ctx context.Context, scene json.RawMessage) (string, error) {
	var out struct {
		SVG string `json:"svg"`
	}
	err := b.do(ctx, "render", http.MethodPost, b.RenderURL+"/render/svg",
		map[string]any{"scene": scene}, &out)
	return out.SVG, err
}

// Export forwards an export request and returns the artifact record.
func (b *Backend) Export(ctx context.Context, kind string, body map[string]any) (map[string]any, error) {
	var out map[string]any
	path := map[string]string{
		"svg":         "/export/svg",
		"png":         "/export/png",
		"icon-bundle": "/export/icon-bundle",
		"json":        "/export/isoforge-json",
	}[kind]
	if path == "" {
		return nil, fmt.Errorf("unknown export kind %q", kind)
	}
	err := b.do(ctx, "render", http.MethodPost, b.RenderURL+path, body, &out)
	return out, err
}

// DiffRender renders both sides of a version comparison.
func (b *Backend) DiffRender(ctx context.Context, before, after json.RawMessage) (map[string]any, error) {
	var out map[string]any
	err := b.do(ctx, "render", http.MethodPost, b.RenderURL+"/diff/render",
		map[string]any{"before": before, "after": after}, &out)
	return out, err
}
