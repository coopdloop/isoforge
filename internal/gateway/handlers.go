package gateway

import (
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/coopdloop/isoforge/internal/isodsl"
	"github.com/coopdloop/isoforge/internal/store"
)

// handleListProjects powers standalone CLI commands, which need to find the most
// recent project with actual content when no session is running.
func (s *Server) handleListProjects(c *gin.Context) {
	projects, err := s.store.ListProjects(parseIntDefault(c.Query("limit"), 50))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"projects": projects})
}

func (s *Server) handleGetScene(c *gin.Context) {
	current, err := s.store.GetCurrentScene(c.Param("project_id"))
	if errors.Is(err, store.ErrNotFound) {
		c.JSON(http.StatusNotFound, gin.H{"error": "no scene for this project yet"})
		return
	}
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, current)
}

func (s *Server) handleHistory(c *gin.Context) {
	limit := parseIntDefault(c.Query("limit"), 100)
	history, err := s.store.History(c.Param("project_id"), limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"history": history, "count": len(history)})
}

func (s *Server) handleGetVersion(c *gin.Context) {
	number, err := strconv.Atoi(c.Param("version"))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "version must be an integer"})
		return
	}
	version, err := s.store.GetSceneVersionByNumber(c.Param("project_id"), number)
	if errors.Is(err, store.ErrNotFound) {
		c.JSON(http.StatusNotFound, gin.H{"error": "version not found"})
		return
	}
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, version)
}

type revertRequest struct {
	Version   int    `json:"version" binding:"required"`
	SessionID string `json:"session_id"`
}

func (s *Server) handleRevert(c *gin.Context) {
	var req revertRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	projectID := c.Param("project_id")
	version, err := s.store.Revert(projectID, req.Version, "user")
	if errors.Is(err, store.ErrNotFound) {
		c.JSON(http.StatusNotFound, gin.H{"error": "version not found"})
		return
	}
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// Keep the agent's working scene in step, or its next patch would apply to the
	// pre-revert design and silently undo the revert.
	if session, ok := s.session(req.SessionID); ok {
		if err := s.backend.ReloadScene(c.Request.Context(), session.ConversationID, version.Scene); err != nil {
			s.log.Warn("could not sync agent after revert", "error", err)
		}
		s.hub.Broadcast(session.ID, EventSceneUpdated, gin.H{
			"scene":      version.Scene,
			"version":    version.VersionNumber,
			"scene_hash": version.SceneHash,
			"summary":    version.ChangeSummary,
			"project_id": projectID,
		})
	}

	c.JSON(http.StatusOK, version)
}

func (s *Server) handleDiff(c *gin.Context) {
	projectID := c.Param("project_id")
	from := parseIntDefault(c.Query("from"), 0)
	to := parseIntDefault(c.Query("to"), 0)

	// Default to comparing the head against its parent, which is what `isoforge diff`
	// with no arguments should mean.
	if from == 0 || to == 0 {
		current, err := s.store.GetCurrentScene(projectID)
		if err != nil {
			c.JSON(http.StatusNotFound, gin.H{"error": "no versions to diff"})
			return
		}
		if to == 0 {
			to = current.VersionNumber
		}
		if from == 0 {
			from = to - 1
		}
	}
	if from < 1 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "nothing to compare against"})
		return
	}

	diff, err := s.store.Diff(projectID, from, to)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
			return
		}
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	if c.Query("render") == "true" {
		before, errA := s.store.GetSceneVersionByNumber(projectID, from)
		after, errB := s.store.GetSceneVersionByNumber(projectID, to)
		if errA == nil && errB == nil {
			if rendered, err := s.backend.DiffRender(c.Request.Context(), before.Scene, after.Scene); err == nil {
				c.JSON(http.StatusOK, gin.H{"diff": diff, "render": rendered})
				return
			}
		}
	}

	c.JSON(http.StatusOK, diff)
}

type renderRequest struct {
	SessionID string          `json:"session_id"`
	ProjectID string          `json:"project_id"`
	Scene     json.RawMessage `json:"scene"`
	Version   int             `json:"version"`
}

// resolveScene finds the scene a request refers to, by explicit payload, version, or
// the project's head.
func (s *Server) resolveScene(req renderRequest) (json.RawMessage, string, error) {
	if len(req.Scene) > 0 {
		return req.Scene, req.ProjectID, nil
	}

	projectID := req.ProjectID
	if projectID == "" {
		session, ok := s.session(req.SessionID)
		if !ok {
			return nil, "", errors.New("provide scene, project_id, or a valid session_id")
		}
		projectID = session.ProjectID
	}

	if req.Version > 0 {
		version, err := s.store.GetSceneVersionByNumber(projectID, req.Version)
		if err != nil {
			return nil, projectID, err
		}
		return version.Scene, projectID, nil
	}

	current, err := s.store.GetCurrentScene(projectID)
	if err != nil {
		return nil, projectID, err
	}
	return current.Scene, projectID, nil
}

func (s *Server) handleRender(c *gin.Context) {
	var req renderRequest
	if err := c.ShouldBindJSON(&req); err != nil && err.Error() != "EOF" {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	scene, _, err := s.resolveScene(req)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
		return
	}

	svg, err := s.backend.RenderSVG(c.Request.Context(), scene)
	if err != nil {
		s.respondBackendError(c, err)
		return
	}
	c.JSON(http.StatusOK, gin.H{"svg": svg})
}

type exportRequest struct {
	renderRequest
	Format   string         `json:"format"`
	Options  map[string]any `json:"options"`
	Filename string         `json:"filename"`
}

func (s *Server) handleExport(c *gin.Context) {
	var req exportRequest
	if err := c.ShouldBindJSON(&req); err != nil && err.Error() != "EOF" {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if req.Format == "" {
		req.Format = "png"
	}

	scene, _, err := s.resolveScene(req.renderRequest)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
		return
	}

	body := map[string]any{"scene": scene}
	for k, v := range req.Options {
		body[k] = v
	}
	if req.Filename != "" {
		body["filename"] = req.Filename
	}

	artifact, err := s.backend.Export(c.Request.Context(), req.Format, body)
	if err != nil {
		s.respondBackendError(c, err)
		return
	}

	if session, ok := s.session(req.SessionID); ok {
		s.hub.Broadcast(session.ID, EventExportComplete, artifact)
	}
	c.JSON(http.StatusOK, artifact)
}

// --- themes -----------------------------------------------------------------

func (s *Server) handleListThemes(c *gin.Context) {
	themes, err := s.store.ListThemes()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"themes": themes})
}

type saveThemeRequest struct {
	Name        string            `json:"name" binding:"required"`
	Description string            `json:"description"`
	Colors      map[string]string `json:"colors" binding:"required"`
	ProjectID   string            `json:"project_id"`
}

func (s *Server) handleSaveTheme(c *gin.Context) {
	var req saveThemeRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	theme, err := s.store.SaveTheme(req.Name, store.ThemeDoc{
		Name:        req.Name,
		Description: req.Description,
		Colors:      req.Colors,
	}, req.ProjectID)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusCreated, theme)
}

func (s *Server) handleDeleteTheme(c *gin.Context) {
	err := s.store.DeleteTheme(c.Param("theme_id"))
	if errors.Is(err, store.ErrNotFound) {
		c.JSON(http.StatusNotFound, gin.H{"error": "theme not found"})
		return
	}
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	c.Status(http.StatusNoContent)
}

func (s *Server) handleImportTheme(c *gin.Context) {
	data, err := io.ReadAll(io.LimitReader(c.Request.Body, 1<<20))
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	theme, err := s.store.ImportTheme(data)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusCreated, theme)
}

// --- schema -----------------------------------------------------------------

func (s *Server) handleSchema(c *gin.Context) {
	schema, err := isodsl.Schema()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, schema)
}

func (s *Server) handleValidate(c *gin.Context) {
	var body struct {
		Scene json.RawMessage `json:"scene"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	// Always 200: an invalid scene is an expected outcome the UI renders inline,
	// not a transport error.
	c.JSON(http.StatusOK, isodsl.ValidateBytes(body.Scene))
}

// --- static -----------------------------------------------------------------

// mountStatic serves the embedded React bundle with SPA fallback.
func (s *Server) mountStatic(r *gin.Engine) {
	if s.webFS == nil {
		r.NoRoute(func(c *gin.Context) {
			c.JSON(http.StatusNotFound, gin.H{
				"error": "not found",
				"hint":  "web UI is not embedded in this build; run the Vite dev server",
			})
		})
		return
	}

	fileServer := http.FileServer(http.FS(s.webFS))
	r.NoRoute(func(c *gin.Context) {
		path := strings.TrimPrefix(c.Request.URL.Path, "/")
		if path == "" {
			path = "index.html"
		}

		if _, err := fs.Stat(s.webFS, path); err != nil {
			// Unknown non-asset paths are client-side routes, so serve the shell and
			// let React Router resolve them.
			if hasFileExtension(path) {
				c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
				return
			}
			c.Request.URL.Path = "/"
		}
		fileServer.ServeHTTP(c.Writer, c.Request)
	})
}

func hasFileExtension(path string) bool {
	base := path
	if idx := strings.LastIndex(path, "/"); idx >= 0 {
		base = path[idx+1:]
	}
	return strings.Contains(base, ".")
}

func (s *Server) handleExportMetadata(c *gin.Context) {
	record, err := s.backend.GetExport(c.Request.Context(), c.Param("export_id"))
	if err != nil {
		s.respondBackendError(c, err)
		return
	}
	c.JSON(http.StatusOK, record)
}

func (s *Server) handleExportDownload(c *gin.Context) {
	resp, err := s.backend.StreamExport(c.Request.Context(), c.Param("export_id"))
	if err != nil {
		s.respondBackendError(c, err)
		return
	}
	defer resp.Body.Close()

	for _, header := range []string{"Content-Type", "Content-Length", "Content-Disposition"} {
		if v := resp.Header.Get(header); v != "" {
			c.Header(header, v)
		}
	}
	c.Status(resp.StatusCode)
	if _, err := io.Copy(c.Writer, resp.Body); err != nil {
		s.log.Warn("export download interrupted", "error", err)
	}
}
