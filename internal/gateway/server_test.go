package gateway

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/coopdloop/isoforge/internal/store"
)

// fakeBackend stands in for the Python service so gateway behaviour can be tested
// without an LLM, a network, or API keys.
type fakeBackend struct {
	mu            sync.Mutex
	turnResponse  TurnResponse
	turnErr       error
	healthErr     error
	reloadCalls   int
	turnCalls     int
	deleteCalls   int
	lastTurnInput TurnRequest
}

func (f *fakeBackend) server(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()

	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		if f.healthErr != nil {
			http.Error(w, f.healthErr.Error(), http.StatusInternalServerError)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"status": "ok"})
	})

	mux.HandleFunc("/conversations", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]any{"id": "conv-1"})
	})

	mux.HandleFunc("/conversations/", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()

		switch {
		case strings.HasSuffix(r.URL.Path, "/turns"):
			f.turnCalls++
			json.NewDecoder(r.Body).Decode(&f.lastTurnInput)
			if f.turnErr != nil {
				w.WriteHeader(http.StatusUnprocessableEntity)
				json.NewEncoder(w).Encode(map[string]any{"error": f.turnErr.Error()})
				return
			}
			json.NewEncoder(w).Encode(f.turnResponse)
		case strings.HasSuffix(r.URL.Path, "/reload"):
			f.reloadCalls++
			json.NewEncoder(w).Encode(map[string]any{"ok": true})
		default:
			if r.Method == http.MethodDelete {
				f.deleteCalls++
			}
			w.WriteHeader(http.StatusNoContent)
		}
	})

	mux.HandleFunc("/render/svg", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"svg": "<svg/>"})
	})
	mux.HandleFunc("/export/png", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"export_id": "exp-1", "artifact_type": "png", "download_url": "/exports/exp-1/download",
		})
	})

	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func testScene(t *testing.T) json.RawMessage {
	t.Helper()
	dir, _ := os.Getwd()
	for i := 0; i < 8; i++ {
		p := filepath.Join(dir, "testdata", "scenes", "valid", "01-single-cube.isoforge.json")
		if data, err := os.ReadFile(p); err == nil {
			return json.RawMessage(data)
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("fixture not found")
	return nil
}

// recoloredScene returns the fixture with a different fill, producing a distinct hash.
func recoloredScene(t *testing.T, fill string) json.RawMessage {
	t.Helper()
	var scene map[string]any
	json.Unmarshal(testScene(t), &scene)
	scene["shapes"].([]any)[0].(map[string]any)["fill"] = fill
	out, _ := json.Marshal(scene)
	return out
}

func setup(t *testing.T) (*Server, *fakeBackend, http.Handler) {
	t.Helper()
	dir := t.TempDir()
	db, err := store.Open(store.Options{
		DBPath:    filepath.Join(dir, "t.db"),
		ScenesDir: filepath.Join(dir, "scenes"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })

	fake := &fakeBackend{}
	backendSrv := fake.server(t)
	srv := NewServer(Config{
		Store:   db,
		Backend: NewBackend(backendSrv.URL, backendSrv.URL),
	})
	return srv, fake, srv.Handler()
}

func do(t *testing.T, h http.Handler, method, path string, body any) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	var reader io.Reader
	if body != nil {
		encoded, _ := json.Marshal(body)
		reader = bytes.NewReader(encoded)
	}
	req := httptest.NewRequest(method, path, reader)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	var out map[string]any
	if rec.Body.Len() > 0 {
		json.Unmarshal(rec.Body.Bytes(), &out)
	}
	return rec, out
}

func createSession(t *testing.T, h http.Handler) (sessionID, projectID string) {
	t.Helper()
	rec, body := do(t, h, http.MethodPost, "/sessions", map[string]any{"name": "test"})
	if rec.Code != http.StatusCreated {
		t.Fatalf("create session: %d %s", rec.Code, rec.Body.String())
	}
	return body["id"].(string), body["project_id"].(string)
}

func TestHealthReportsBothLayers(t *testing.T) {
	_, _, h := setup(t)
	rec, body := do(t, h, http.MethodGet, "/health", nil)

	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if body["status"] != "ok" || body["store"] != "ok" {
		t.Errorf("unexpected health: %v", body)
	}
}

// TestHealthDegradesWhenBackendDown covers ADR-002: the gateway must survive a
// backend crash and report it, not fall over.
func TestHealthDegradesWhenBackendDown(t *testing.T) {
	_, fake, h := setup(t)
	fake.mu.Lock()
	fake.healthErr = fmt.Errorf("boom")
	fake.mu.Unlock()

	rec, body := do(t, h, http.MethodGet, "/health", nil)
	if rec.Code != http.StatusOK {
		t.Errorf("gateway should stay up, got %d", rec.Code)
	}
	if body["status"] != "degraded" {
		t.Errorf("expected degraded, got %v", body["status"])
	}
}

func TestSessionLifecycle(t *testing.T) {
	_, fake, h := setup(t)
	sessionID, projectID := createSession(t, h)

	if sessionID == "" || projectID == "" {
		t.Fatal("session missing ids")
	}

	rec, _ := do(t, h, http.MethodGet, "/sessions/"+sessionID, nil)
	if rec.Code != http.StatusOK {
		t.Errorf("get session: %d", rec.Code)
	}

	rec, _ = do(t, h, http.MethodDelete, "/sessions/"+sessionID, nil)
	if rec.Code != http.StatusNoContent {
		t.Errorf("delete session: %d", rec.Code)
	}
	if fake.deleteCalls != 1 {
		t.Error("agent conversation was not ended")
	}

	rec, _ = do(t, h, http.MethodGet, "/sessions/"+sessionID, nil)
	if rec.Code != http.StatusNotFound {
		t.Errorf("ended session should 404, got %d", rec.Code)
	}
}

func TestUnknownSession404s(t *testing.T) {
	_, _, h := setup(t)
	rec, _ := do(t, h, http.MethodGet, "/sessions/nope", nil)
	if rec.Code != http.StatusNotFound {
		t.Errorf("status %d", rec.Code)
	}
}

func TestSessionWithSeedSceneCreatesVersion(t *testing.T) {
	_, _, h := setup(t)
	rec, body := do(t, h, http.MethodPost, "/sessions", map[string]any{
		"name": "resumed", "scene": testScene(t),
	})
	if rec.Code != http.StatusCreated {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}

	rec, hist := do(t, h, http.MethodGet, "/scenes/"+body["project_id"].(string)+"/history", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("history: %d", rec.Code)
	}
	if hist["count"].(float64) != 1 {
		t.Errorf("seed scene should create v1, got %v versions", hist["count"])
	}
}

func TestSeedSceneIsValidated(t *testing.T) {
	_, _, h := setup(t)
	rec, _ := do(t, h, http.MethodPost, "/sessions", map[string]any{
		"scene": map[string]any{"isodsl_version": "1.0.0"},
	})
	if rec.Code != http.StatusBadRequest {
		t.Errorf("invalid seed scene should 400, got %d", rec.Code)
	}
}

func TestMessagePersistsVersionAndBroadcasts(t *testing.T) {
	srv, fake, h := setup(t)
	sessionID, projectID := createSession(t, h)

	fake.mu.Lock()
	fake.turnResponse = TurnResponse{
		Reply: "done", Scene: testScene(t), Changed: true,
		IsFullScene: true, Summary: "initial design",
	}
	fake.mu.Unlock()

	rec, body := do(t, h, http.MethodPost, "/sessions/"+sessionID+"/messages",
		map[string]any{"message": "a purple cube"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	if body["version"].(float64) != 1 {
		t.Errorf("expected v1, got %v", body["version"])
	}

	current, err := srv.store.GetCurrentScene(projectID)
	if err != nil {
		t.Fatalf("version not persisted: %v", err)
	}
	if current.ChangeSummary != "initial design" {
		t.Errorf("summary = %q", current.ChangeSummary)
	}
}

// TestConversationalTurnCreatesNoVersion guards against history filling up with
// no-op entries when the user just asks a question.
func TestConversationalTurnCreatesNoVersion(t *testing.T) {
	_, fake, h := setup(t)
	sessionID, projectID := createSession(t, h)

	fake.mu.Lock()
	fake.turnResponse = TurnResponse{Reply: "It is a cube.", Changed: false}
	fake.mu.Unlock()

	rec, body := do(t, h, http.MethodPost, "/sessions/"+sessionID+"/messages",
		map[string]any{"message": "what is this?"})
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if _, present := body["version"]; present {
		t.Error("unchanged turn should not create a version")
	}

	_, hist := do(t, h, http.MethodGet, "/scenes/"+projectID+"/history", nil)
	if hist["count"].(float64) != 0 {
		t.Errorf("expected no versions, got %v", hist["count"])
	}
}

func TestMessageRequiresText(t *testing.T) {
	_, _, h := setup(t)
	sessionID, _ := createSession(t, h)

	rec, _ := do(t, h, http.MethodPost, "/sessions/"+sessionID+"/messages",
		map[string]any{"message": ""})
	if rec.Code != http.StatusBadRequest {
		t.Errorf("empty message should 400, got %d", rec.Code)
	}
}

func TestBackendFailureSurfacesAsServiceUnavailable(t *testing.T) {
	dir := t.TempDir()
	db, _ := store.Open(store.Options{
		DBPath: filepath.Join(dir, "t.db"), ScenesDir: filepath.Join(dir, "s"),
	})
	t.Cleanup(func() { db.Close() })

	// Point at a port nothing is listening on.
	srv := NewServer(Config{
		Store:   db,
		Backend: NewBackend("http://127.0.0.1:1", "http://127.0.0.1:1"),
	})
	h := srv.Handler()

	rec, body := do(t, h, http.MethodPost, "/sessions", map[string]any{"name": "x"})
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", rec.Code)
	}
	if body["hint"] == nil {
		t.Error("unavailable backend should include an actionable hint")
	}
}

func TestHistoryDiffAndRevertFlow(t *testing.T) {
	_, fake, h := setup(t)
	sessionID, projectID := createSession(t, h)

	for i, fill := range []string{"@palette.base", "@palette.accent", "#123456"} {
		fake.mu.Lock()
		fake.turnResponse = TurnResponse{
			Scene: recoloredScene(t, fill), Changed: true,
			Summary: fmt.Sprintf("change %d", i+1), IsFullScene: true,
		}
		fake.mu.Unlock()
		do(t, h, http.MethodPost, "/sessions/"+sessionID+"/messages",
			map[string]any{"message": "recolor"})
	}

	_, hist := do(t, h, http.MethodGet, "/scenes/"+projectID+"/history", nil)
	if hist["count"].(float64) != 3 {
		t.Fatalf("expected 3 versions, got %v", hist["count"])
	}

	rec, diff := do(t, h, http.MethodGet, "/scenes/"+projectID+"/diff?from=1&to=2", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("diff: %d", rec.Code)
	}
	if diff["changed"] != true {
		t.Error("expected a change between v1 and v2")
	}

	rec, reverted := do(t, h, http.MethodPost, "/scenes/"+projectID+"/revert",
		map[string]any{"version": 1, "session_id": sessionID})
	if rec.Code != http.StatusOK {
		t.Fatalf("revert: %d %s", rec.Code, rec.Body.String())
	}
	if reverted["version_number"].(float64) != 4 {
		t.Errorf("revert should append v4, got %v", reverted["version_number"])
	}

	// The agent must be resynced, or its next patch would undo the revert.
	fake.mu.Lock()
	reloads := fake.reloadCalls
	fake.mu.Unlock()
	if reloads != 1 {
		t.Errorf("expected agent reload after revert, got %d calls", reloads)
	}
}

func TestDiffDefaultsToHeadVersusParent(t *testing.T) {
	_, fake, h := setup(t)
	sessionID, projectID := createSession(t, h)

	for _, fill := range []string{"@palette.base", "@palette.accent"} {
		fake.mu.Lock()
		fake.turnResponse = TurnResponse{Scene: recoloredScene(t, fill), Changed: true}
		fake.mu.Unlock()
		do(t, h, http.MethodPost, "/sessions/"+sessionID+"/messages",
			map[string]any{"message": "x"})
	}

	rec, diff := do(t, h, http.MethodGet, "/scenes/"+projectID+"/diff", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if diff["from_version"].(float64) != 1 || diff["to_version"].(float64) != 2 {
		t.Errorf("expected v1->v2, got %v->%v", diff["from_version"], diff["to_version"])
	}
}

func TestRevertToUnknownVersion404s(t *testing.T) {
	_, _, h := setup(t)
	sessionID, projectID := createSession(t, h)
	rec, _ := do(t, h, http.MethodPost, "/scenes/"+projectID+"/revert",
		map[string]any{"version": 99, "session_id": sessionID})
	if rec.Code != http.StatusNotFound {
		t.Errorf("status %d", rec.Code)
	}
}

func TestThemeEndpoints(t *testing.T) {
	_, _, h := setup(t)

	rec, body := do(t, h, http.MethodGet, "/themes", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("list themes: %d", rec.Code)
	}
	if len(body["themes"].([]any)) < 5 {
		t.Error("builtin themes should be seeded")
	}

	rec, saved := do(t, h, http.MethodPost, "/themes", map[string]any{
		"name": "mybrand", "colors": map[string]string{"primary": "#FF0000"},
	})
	if rec.Code != http.StatusCreated {
		t.Fatalf("save theme: %d %s", rec.Code, rec.Body.String())
	}

	rec, _ = do(t, h, http.MethodDelete, "/themes/"+saved["id"].(string), nil)
	if rec.Code != http.StatusNoContent {
		t.Errorf("delete theme: %d", rec.Code)
	}
}

func TestBuiltinThemeCannotBeOverwritten(t *testing.T) {
	_, _, h := setup(t)
	rec, _ := do(t, h, http.MethodPost, "/themes", map[string]any{
		"name": "nord", "colors": map[string]string{"x": "#FFFFFF"},
	})
	if rec.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", rec.Code)
	}
}

func TestValidateEndpointReturns200ForInvalidScene(t *testing.T) {
	_, _, h := setup(t)
	rec, body := do(t, h, http.MethodPost, "/schema/validate", map[string]any{
		"scene": map[string]any{"isodsl_version": "1.0.0"},
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("validation verdict should be 200, got %d", rec.Code)
	}
	if body["valid"] != false {
		t.Error("expected valid=false")
	}
}

func TestSchemaIsServed(t *testing.T) {
	_, _, h := setup(t)
	rec, body := do(t, h, http.MethodGet, "/schema/isodsl", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if body["title"] != "IsoDSL Scene" {
		t.Errorf("unexpected schema: %v", body["title"])
	}
}

func TestExportForwardsToBackend(t *testing.T) {
	_, fake, h := setup(t)
	sessionID, _ := createSession(t, h)

	fake.mu.Lock()
	fake.turnResponse = TurnResponse{Scene: testScene(t), Changed: true}
	fake.mu.Unlock()
	do(t, h, http.MethodPost, "/sessions/"+sessionID+"/messages", map[string]any{"message": "x"})

	rec, body := do(t, h, http.MethodPost, "/export", map[string]any{
		"session_id": sessionID, "format": "png",
	})
	if rec.Code != http.StatusOK {
		t.Fatalf("export: %d %s", rec.Code, rec.Body.String())
	}
	if body["artifact_type"] != "png" {
		t.Errorf("unexpected artifact: %v", body)
	}
}

func TestExportWithoutSceneFails(t *testing.T) {
	_, _, h := setup(t)
	rec, _ := do(t, h, http.MethodPost, "/export", map[string]any{"format": "png"})
	if rec.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", rec.Code)
	}
}

func TestHubBroadcastDropsSlowClients(t *testing.T) {
	hub := NewHub(nil)
	slow := &client{hub: hub, sessionID: "s1", send: make(chan []byte)} // unbuffered
	hub.add(slow)

	done := make(chan struct{})
	go func() {
		hub.Broadcast("s1", EventSceneUpdated, map[string]string{"a": "b"})
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("broadcast blocked on a slow client")
	}
	if hub.ClientCount("s1") != 0 {
		t.Error("slow client should have been dropped")
	}
}

func TestHubDeliversToMultipleClients(t *testing.T) {
	hub := NewHub(nil)
	a := &client{hub: hub, sessionID: "s1", send: make(chan []byte, 4)}
	b := &client{hub: hub, sessionID: "s1", send: make(chan []byte, 4)}
	other := &client{hub: hub, sessionID: "s2", send: make(chan []byte, 4)}
	hub.add(a)
	hub.add(b)
	hub.add(other)

	hub.Broadcast("s1", EventSceneUpdated, map[string]int{"version": 2})

	if len(a.send) != 1 || len(b.send) != 1 {
		t.Error("both session clients should receive the event")
	}
	if len(other.send) != 0 {
		t.Error("event leaked to a different session")
	}
}

func TestHubCloseSessionDisconnectsClients(t *testing.T) {
	hub := NewHub(nil)
	c := &client{hub: hub, sessionID: "s1", send: make(chan []byte, 4)}
	hub.add(c)

	hub.CloseSession("s1")
	if hub.ClientCount("s1") != 0 {
		t.Error("clients should be removed")
	}
}

// setupWithWeb builds a server with a fake embedded bundle.
func setupWithWeb(t *testing.T) http.Handler {
	t.Helper()
	dir := t.TempDir()
	db, err := store.Open(store.Options{
		DBPath: filepath.Join(dir, "t.db"), ScenesDir: filepath.Join(dir, "s"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })

	webRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(webRoot, "index.html"),
		[]byte("<!doctype html><title>IsoForge</title>"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(webRoot, "assets"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(webRoot, "assets", "app.js"),
		[]byte("console.log(1)"), 0o644); err != nil {
		t.Fatal(err)
	}

	fake := &fakeBackend{}
	backendSrv := fake.server(t)
	srv := NewServer(Config{
		Store:   db,
		Backend: NewBackend(backendSrv.URL, backendSrv.URL),
		WebFS:   os.DirFS(webRoot),
	})
	return srv.Handler()
}

func getWithAccept(t *testing.T, h http.Handler, path, accept string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	req.Header.Set("Accept", accept)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

// TestSpaFallbackVsApiCollision covers the case where a path is both an API route and
// a client-side route: GET /sessions/:id must return JSON to the CLI but render the
// workbench when typed into a browser.
func TestSpaFallbackVsApiCollision(t *testing.T) {
	h := setupWithWeb(t)

	browser := getWithAccept(t, h, "/sessions/unknown-id", "text/html,application/xhtml+xml")
	if browser.Code != http.StatusOK {
		t.Errorf("browser navigation should render the SPA, got %d", browser.Code)
	}
	if ct := browser.Header().Get("Content-Type"); !strings.Contains(ct, "text/html") {
		t.Errorf("expected html, got %q", ct)
	}

	apiCall := getWithAccept(t, h, "/sessions/unknown-id", "application/json")
	if apiCall.Code != http.StatusNotFound {
		t.Errorf("API call should 404, got %d", apiCall.Code)
	}
}

func TestSpaServesClientRoutes(t *testing.T) {
	h := setupWithWeb(t)
	for _, path := range []string{"/", "/themes", "/sessions/abc/history", "/deep/unknown/route"} {
		rec := getWithAccept(t, h, path, "text/html")
		if rec.Code != http.StatusOK {
			t.Errorf("%s: expected 200, got %d", path, rec.Code)
		}
	}
}

func TestStaticAssetsAreServed(t *testing.T) {
	h := setupWithWeb(t)
	rec := getWithAccept(t, h, "/assets/app.js", "*/*")
	if rec.Code != http.StatusOK {
		t.Fatalf("asset should be served, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "console.log") {
		t.Error("unexpected asset body")
	}
}

// TestMissingAssetDoesNotReturnShell guards against the subtle failure where a broken
// asset path silently returns index.html and the browser reports a syntax error.
func TestMissingAssetDoesNotReturnShell(t *testing.T) {
	h := setupWithWeb(t)
	rec := getWithAccept(t, h, "/assets/missing.js", "text/html")
	if rec.Code != http.StatusNotFound {
		t.Errorf("missing asset should 404, got %d", rec.Code)
	}
}

func TestApiStillWorksWithWebMounted(t *testing.T) {
	h := setupWithWeb(t)
	rec := getWithAccept(t, h, "/themes", "application/json")
	if rec.Code != http.StatusOK {
		t.Fatalf("themes API broke with web mounted: %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "themes") {
		t.Error("expected theme JSON")
	}
}
