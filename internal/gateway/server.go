package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"log/slog"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"github.com/gorilla/websocket"

	"github.com/coopdloop/isoforge/internal/isodsl"
	"github.com/coopdloop/isoforge/internal/store"
)

// Session ties a CLI run, an agent conversation and a project together.
type Session struct {
	ID             string `json:"id"`
	ProjectID      string `json:"project_id"`
	ConversationID string `json:"conversation_id"`
	Title          string `json:"title,omitempty"`
	CreatedAt      string `json:"created_at"`
	LastSeenAt     string `json:"last_seen_at"`
	Ended          bool   `json:"ended"`
}

// Server is the gateway HTTP surface.
type Server struct {
	store    *store.Store
	backend  *Backend
	hub      *Hub
	log      *slog.Logger
	webFS    fs.FS
	upgrader websocket.Upgrader

	mu       sync.RWMutex
	sessions map[string]*Session
}

// Config configures the gateway.
type Config struct {
	Store   *store.Store
	Backend *Backend
	Logger  *slog.Logger
	// WebFS serves the built React bundle. Nil disables static serving, which is
	// what the CLI does when running against a Vite dev server.
	WebFS fs.FS
}

// NewServer wires the gateway.
func NewServer(cfg Config) *Server {
	log := cfg.Logger
	if log == nil {
		log = slog.Default()
	}
	return &Server{
		store:    cfg.Store,
		backend:  cfg.Backend,
		hub:      NewHub(log),
		log:      log,
		webFS:    cfg.WebFS,
		sessions: make(map[string]*Session),
		upgrader: websocket.Upgrader{
			ReadBufferSize:  1024,
			WriteBufferSize: 4096,
			// Localhost-only single-user tool with no auth and no cookies, so there
			// is no cross-origin state for an attacker to leverage here.
			CheckOrigin: func(r *http.Request) bool { return true },
		},
	}
}

// Handler builds the router.
func (s *Server) Handler() http.Handler {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery(), s.requestLogger())

	r.GET("/health", s.handleHealth)
	r.GET("/ws/preview/:session_id", s.handlePreviewSocket)

	r.POST("/sessions", s.handleCreateSession)
	r.GET("/sessions", s.handleListSessions)
	r.GET("/sessions/:session_id", s.handleGetSession)
	r.DELETE("/sessions/:session_id", s.handleEndSession)
	r.POST("/sessions/:session_id/messages", s.handleMessage)

	r.GET("/projects", s.handleListProjects)
	r.GET("/scenes/:project_id", s.handleGetScene)
	r.GET("/scenes/:project_id/history", s.handleHistory)
	r.GET("/scenes/:project_id/versions/:version", s.handleGetVersion)
	r.POST("/scenes/:project_id/revert", s.handleRevert)
	r.GET("/scenes/:project_id/diff", s.handleDiff)

	r.POST("/render", s.handleRender)
	r.POST("/export", s.handleExport)
	// Export artifacts live on the render service's filesystem. The gateway is the
	// only endpoint clients know about (ADR-002), so it proxies downloads through.
	r.GET("/exports/:export_id", s.handleExportMetadata)
	r.GET("/exports/:export_id/download", s.handleExportDownload)

	r.GET("/themes", s.handleListThemes)
	r.POST("/themes", s.handleSaveTheme)
	r.DELETE("/themes/:theme_id", s.handleDeleteTheme)
	r.POST("/themes/import", s.handleImportTheme)

	r.GET("/schema/isodsl", s.handleSchema)
	r.POST("/schema/validate", s.handleValidate)

	s.mountStatic(r)
	return r
}

func (s *Server) requestLogger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		// Preview sockets are long-lived; logging their duration is noise.
		if c.Request.URL.Path != "" && c.Writer.Status() >= 400 {
			s.log.Warn("request failed",
				"method", c.Request.Method, "path", c.Request.URL.Path,
				"status", c.Writer.Status(), "duration", time.Since(start))
			return
		}
		s.log.Debug("request",
			"method", c.Request.Method, "path", c.Request.URL.Path,
			"status", c.Writer.Status(), "duration", time.Since(start))
	}
}

func (s *Server) handleHealth(c *gin.Context) {
	status := gin.H{
		"status":          "ok",
		"isodsl_version":  isodsl.SchemaVersion,
		"preview_clients": s.hub.TotalClients(),
		"sessions":        len(s.sessions),
	}

	if err := s.store.Ping(); err != nil {
		status["status"] = "degraded"
		status["store"] = err.Error()
	} else {
		status["store"] = "ok"
	}

	if backend, err := s.backend.Health(c.Request.Context()); err != nil {
		// Report rather than fail: the gateway staying up while a backend restarts
		// is the whole point of ADR-002's resilience requirement.
		status["status"] = "degraded"
		status["backend"] = gin.H{"status": "unreachable", "error": err.Error()}
	} else {
		status["backend"] = backend
	}

	c.JSON(http.StatusOK, status)
}

// --- sessions ---------------------------------------------------------------

type createSessionRequest struct {
	ProjectID   string          `json:"project_id"`
	Name        string          `json:"name"`
	Description string          `json:"description"`
	Scene       json.RawMessage `json:"scene"`
}

func (s *Server) handleCreateSession(c *gin.Context) {
	var req createSessionRequest
	if err := c.ShouldBindJSON(&req); err != nil && err.Error() != "EOF" {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	projectID := req.ProjectID
	if projectID == "" {
		name := req.Name
		if name == "" {
			name = "Untitled logo"
		}
		project, err := s.store.CreateProject(name, req.Description)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		projectID = project.ID
	} else if _, err := s.store.GetProject(projectID); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "project not found"})
		return
	}

	// Seeding a scene at session start is how `isoforge chat --continue` resumes.
	if len(req.Scene) > 0 {
		if result := isodsl.ValidateBytes(req.Scene); !result.Valid {
			c.JSON(http.StatusBadRequest, gin.H{
				"error": "seed scene failed validation", "errors": result.Errors,
			})
			return
		}
		var scene any
		_ = json.Unmarshal(req.Scene, &scene)
		if _, err := s.store.SaveScene(store.SaveSceneOptions{
			ProjectID: projectID, Scene: scene,
			ChangeSummary: "Imported scene", CreatedBy: "import",
		}); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
	}

	conversationID, err := s.backend.CreateConversation(
		c.Request.Context(), projectID, req.Name, req.Scene)
	if err != nil {
		s.respondBackendError(c, err)
		return
	}

	now := time.Now().UTC().Format(time.RFC3339)
	session := &Session{
		ID:             uuid.NewString(),
		ProjectID:      projectID,
		ConversationID: conversationID,
		Title:          req.Name,
		CreatedAt:      now,
		LastSeenAt:     now,
	}

	s.mu.Lock()
	s.sessions[session.ID] = session
	s.mu.Unlock()

	c.JSON(http.StatusCreated, session)
}

func (s *Server) handleListSessions(c *gin.Context) {
	s.mu.RLock()
	out := make([]*Session, 0, len(s.sessions))
	for _, session := range s.sessions {
		out = append(out, session)
	}
	s.mu.RUnlock()
	c.JSON(http.StatusOK, gin.H{"sessions": out})
}

func (s *Server) session(id string) (*Session, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	session, ok := s.sessions[id]
	return session, ok
}

func (s *Server) handleGetSession(c *gin.Context) {
	session, ok := s.session(c.Param("session_id"))
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "session not found"})
		return
	}

	response := gin.H{"session": session, "preview_clients": s.hub.ClientCount(session.ID)}
	if current, err := s.store.GetCurrentScene(session.ProjectID); err == nil {
		response["scene"] = current.Scene
		response["version"] = current.VersionNumber
		response["scene_hash"] = current.SceneHash
	}
	c.JSON(http.StatusOK, response)
}

func (s *Server) handleEndSession(c *gin.Context) {
	id := c.Param("session_id")
	session, ok := s.session(id)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "session not found"})
		return
	}

	// Best-effort: a dead agent must not prevent the user ending their session.
	if err := s.backend.DeleteConversation(c.Request.Context(), session.ConversationID); err != nil {
		s.log.Warn("could not end agent conversation", "error", err)
	}

	s.mu.Lock()
	session.Ended = true
	delete(s.sessions, id)
	s.mu.Unlock()

	s.hub.CloseSession(id)
	c.Status(http.StatusNoContent)
}

// --- chat -------------------------------------------------------------------

type messageRequest struct {
	Message          string  `json:"message" binding:"required"`
	ModelOverride    string  `json:"model_override"`
	ProviderOverride string  `json:"provider_override"`
	Temperature      float64 `json:"temperature"`
}

// handleMessage is the core orchestration: agent turn, persist version, broadcast.
func (s *Server) handleMessage(c *gin.Context) {
	session, ok := s.session(c.Param("session_id"))
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "session not found"})
		return
	}

	var req messageRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	turn, err := s.backend.RunTurn(c.Request.Context(), session.ConversationID, TurnRequest{
		Message:          req.Message,
		ModelOverride:    req.ModelOverride,
		ProviderOverride: req.ProviderOverride,
		Temperature:      req.Temperature,
	})
	if err != nil {
		var be *BackendError
		if errors.As(err, &be) && !be.Unavailable() {
			s.hub.Broadcast(session.ID, EventValidationFailed, json.RawMessage(be.Body))
		}
		s.respondBackendError(c, err)
		return
	}

	response := gin.H{
		"reply":           turn.Reply,
		"changed":         turn.Changed,
		"is_full_scene":   turn.IsFullScene,
		"summary":         turn.Summary,
		"repair_attempts": turn.RepairAttempts,
		"usage":           turn.Usage,
	}

	// A conversational turn that changed nothing must not create a version.
	if turn.Changed && len(turn.Scene) > 0 {
		var scene any
		if err := json.Unmarshal(turn.Scene, &scene); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "agent returned unparseable scene"})
			return
		}

		var patch any
		if len(turn.Patch) > 0 {
			patch = turn.Patch
		}

		version, err := s.store.SaveScene(store.SaveSceneOptions{
			ProjectID:      session.ProjectID,
			Scene:          scene,
			Patch:          patch,
			ConversationID: session.ConversationID,
			ChangeSummary:  turn.Summary,
			CreatedBy:      "agent",
		})
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}

		response["version"] = version.VersionNumber
		response["scene"] = version.Scene
		response["scene_hash"] = version.SceneHash

		s.hub.Broadcast(session.ID, EventSceneUpdated, gin.H{
			"scene":         version.Scene,
			"version":       version.VersionNumber,
			"scene_hash":    version.SceneHash,
			"summary":       version.ChangeSummary,
			"project_id":    session.ProjectID,
			"is_full_scene": turn.IsFullScene,
		})
	}

	if turn.ThemeSaved != nil {
		if _, err := s.store.SaveTheme(turn.ThemeSaved.Name, store.ThemeDoc{
			Name:   turn.ThemeSaved.Name,
			Colors: turn.ThemeSaved.Colors,
		}, session.ProjectID); err != nil {
			s.log.Warn("could not save theme", "error", err)
		} else {
			response["theme_saved"] = turn.ThemeSaved.Name
		}
	}

	s.touch(session)
	c.JSON(http.StatusOK, response)
}

func (s *Server) touch(session *Session) {
	s.mu.Lock()
	session.LastSeenAt = time.Now().UTC().Format(time.RFC3339)
	s.mu.Unlock()
}

// --- preview socket ---------------------------------------------------------

func (s *Server) handlePreviewSocket(c *gin.Context) {
	sessionID := c.Param("session_id")

	conn, err := s.upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		s.log.Warn("websocket upgrade failed", "error", err)
		return
	}

	client := &client{
		hub:       s.hub,
		conn:      conn,
		sessionID: sessionID,
		send:      make(chan []byte, sendBuffer),
	}
	s.hub.add(client)

	go client.writePump()

	// Send the current scene immediately so a newly opened tab is never blank while
	// it waits for the next turn.
	payload := gin.H{"session_id": sessionID}
	if session, ok := s.session(sessionID); ok {
		if current, err := s.store.GetCurrentScene(session.ProjectID); err == nil {
			payload["scene"] = current.Scene
			payload["version"] = current.VersionNumber
			payload["scene_hash"] = current.SceneHash
		}
	}
	s.hub.Broadcast(sessionID, EventConnected, payload)

	client.readPump()
}

func (s *Server) respondBackendError(c *gin.Context, err error) {
	var be *BackendError
	if errors.As(err, &be) {
		if be.Unavailable() {
			c.JSON(http.StatusServiceUnavailable, gin.H{
				"error":   be.Error(),
				"service": be.Service,
				"hint":    "the local " + be.Service + " service is not responding; it may have crashed",
			})
			return
		}
		status := be.StatusCode
		if status < 400 || status > 599 {
			status = http.StatusBadGateway
		}
		var body any
		if json.Unmarshal(be.Body, &body) == nil {
			c.JSON(status, body)
			return
		}
		c.JSON(status, gin.H{"error": be.Error()})
		return
	}
	c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
}

func parseIntDefault(raw string, fallback int) int {
	if raw == "" {
		return fallback
	}
	if v, err := strconv.Atoi(raw); err == nil {
		return v
	}
	return fallback
}

// Shutdown releases resources.
func (s *Server) Shutdown(ctx context.Context) error {
	s.mu.Lock()
	ids := make([]string, 0, len(s.sessions))
	for id := range s.sessions {
		ids = append(ids, id)
	}
	s.mu.Unlock()

	for _, id := range ids {
		s.hub.CloseSession(id)
	}
	return nil
}
