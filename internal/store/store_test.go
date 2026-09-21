package store

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/coopdloop/isoforge/internal/isodsl"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	dir := t.TempDir()
	s, err := Open(Options{
		DBPath:    filepath.Join(dir, "test.db"),
		ScenesDir: filepath.Join(dir, "scenes"),
	})
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

// fixtureScene loads a known-good scene from the shared fixture suite.
func fixtureScene(t *testing.T, name string) map[string]any {
	t.Helper()
	dir, _ := os.Getwd()
	for i := 0; i < 8; i++ {
		candidate := filepath.Join(dir, "testdata", "scenes", "valid", name)
		if data, err := os.ReadFile(candidate); err == nil {
			var scene map[string]any
			if err := json.Unmarshal(data, &scene); err != nil {
				t.Fatal(err)
			}
			return scene
		}
		dir = filepath.Dir(dir)
	}
	t.Fatalf("fixture %s not found", name)
	return nil
}

func singleCube(t *testing.T) map[string]any {
	return fixtureScene(t, "01-single-cube.isoforge.json")
}

// recolor returns a copy of the scene with the first shape's fill changed.
func recolor(t *testing.T, scene map[string]any, fill string) map[string]any {
	t.Helper()
	encoded, _ := json.Marshal(scene)
	var out map[string]any
	if err := json.Unmarshal(encoded, &out); err != nil {
		t.Fatal(err)
	}
	out["shapes"].([]any)[0].(map[string]any)["fill"] = fill
	return out
}

func TestProjectLifecycle(t *testing.T) {
	s := testStore(t)

	p, err := s.CreateProject("My Logo", "a test project")
	if err != nil {
		t.Fatal(err)
	}
	if p.ID == "" || p.VersionCount != 0 {
		t.Fatalf("unexpected new project: %+v", p)
	}

	fetched, err := s.GetProject(p.ID)
	if err != nil {
		t.Fatal(err)
	}
	if fetched.Name != "My Logo" {
		t.Errorf("name = %q", fetched.Name)
	}

	if _, err := s.GetProject("nope"); !errors.Is(err, ErrNotFound) {
		t.Errorf("expected ErrNotFound, got %v", err)
	}

	list, err := s.ListProjects(10)
	if err != nil || len(list) != 1 {
		t.Fatalf("list = %v, err = %v", list, err)
	}

	if err := s.DeleteProject(p.ID); err != nil {
		t.Fatal(err)
	}
	if err := s.DeleteProject(p.ID); !errors.Is(err, ErrNotFound) {
		t.Errorf("second delete should report not found, got %v", err)
	}
}

func TestSaveSceneRejectsInvalidDocument(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")

	broken := singleCube(t)
	broken["shapes"].([]any)[0].(map[string]any)["fill"] = "@palette.ghost"

	_, err := s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: broken})
	if err == nil {
		t.Fatal("invalid scene must not reach storage")
	}
	var ve *isodsl.ValidationError
	if !errors.As(err, &ve) {
		t.Fatalf("expected ValidationError, got %T: %v", err, err)
	}
	if ve.Result.Errors[0].Code != isodsl.CodeUnknownPaletteRef {
		t.Errorf("expected ISO002, got %s", ve.Result.Errors[0].Code)
	}
}

func TestVersionsAreImmutableAndSequential(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	scene := singleCube(t)

	v1, err := s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene, ChangeSummary: "first"})
	if err != nil {
		t.Fatal(err)
	}
	v2, err := s.SaveScene(SaveSceneOptions{
		ProjectID: p.ID, Scene: recolor(t, scene, "@palette.accent"), ChangeSummary: "recolor",
	})
	if err != nil {
		t.Fatal(err)
	}

	if v1.VersionNumber != 1 || v2.VersionNumber != 2 {
		t.Errorf("version numbers = %d, %d", v1.VersionNumber, v2.VersionNumber)
	}
	if v2.ParentVersionID != v1.ID {
		t.Error("v2 should descend from v1")
	}

	// v1 must be unchanged by v2's arrival.
	reloaded, err := s.GetSceneVersion(v1.ID)
	if err != nil {
		t.Fatal(err)
	}
	if reloaded.SceneHash != v1.SceneHash {
		t.Error("earlier version was mutated")
	}

	current, err := s.GetCurrentScene(p.ID)
	if err != nil {
		t.Fatal(err)
	}
	if current.ID != v2.ID {
		t.Error("head pointer did not advance")
	}
}

func TestSceneHashMatchesCanonicalForm(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	scene := singleCube(t)

	v, err := s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene})
	if err != nil {
		t.Fatal(err)
	}
	expected, _ := isodsl.SceneHash(scene)
	if v.SceneHash != expected {
		t.Errorf("stored hash %s != canonical hash %s", v.SceneHash, expected)
	}
}

func TestSceneFilesAreWrittenToDisk(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	if _, err := s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: singleCube(t)}); err != nil {
		t.Fatal(err)
	}

	for _, name := range []string{"v1.isoforge.json", "latest.isoforge.json"} {
		path := filepath.Join(s.ScenesDir(), p.ID, name)
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("expected %s on disk: %v", name, err)
		}
		if !json.Valid(data) {
			t.Errorf("%s is not valid JSON", name)
		}
	}
}

func TestHistoryOrdersNewestFirst(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	scene := singleCube(t)

	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene, ChangeSummary: "one"})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: recolor(t, scene, "@palette.accent"), ChangeSummary: "two"})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: recolor(t, scene, "#123456"), ChangeSummary: "three"})

	history, err := s.History(p.ID, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(history) != 3 {
		t.Fatalf("expected 3 entries, got %d", len(history))
	}
	if history[0].VersionNumber != 3 || history[2].VersionNumber != 1 {
		t.Error("history is not newest-first")
	}
	if !history[0].IsCurrent || history[1].IsCurrent {
		t.Error("exactly the head version should be marked current")
	}
	if history[0].ShapeCount != 1 {
		t.Errorf("shape count = %d, want 1", history[0].ShapeCount)
	}
}

// TestRevertPreservesHistory covers the deliberate design choice that a revert is
// an append, not a truncation, so the user can always undo the undo.
func TestRevertPreservesHistory(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	scene := singleCube(t)

	v1, _ := s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: recolor(t, scene, "@palette.accent")})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: recolor(t, scene, "#123456")})

	reverted, err := s.Revert(p.ID, 1, "cli")
	if err != nil {
		t.Fatal(err)
	}

	if reverted.VersionNumber != 4 {
		t.Errorf("revert should append v4, got v%d", reverted.VersionNumber)
	}
	if reverted.SceneHash != v1.SceneHash {
		t.Error("reverted content should equal the target version")
	}

	history, _ := s.History(p.ID, 10)
	if len(history) != 4 {
		t.Errorf("revert must not truncate history, got %d entries", len(history))
	}
	if _, err := s.GetSceneVersionByNumber(p.ID, 3); err != nil {
		t.Error("v3 should still be reachable after reverting past it")
	}
}

func TestRevertToMissingVersionFails(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: singleCube(t)})

	if _, err := s.Revert(p.ID, 99, "cli"); !errors.Is(err, ErrNotFound) {
		t.Errorf("expected ErrNotFound, got %v", err)
	}
}

func TestDiffDetectsRecolor(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	scene := singleCube(t)

	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: recolor(t, scene, "@palette.accent")})

	diff, err := s.Diff(p.ID, 1, 2)
	if err != nil {
		t.Fatal(err)
	}
	if !diff.Changed {
		t.Fatal("expected a change")
	}
	var ops []patchOp
	json.Unmarshal(diff.Patch, &ops)
	if len(ops) != 1 || ops[0].Op != "replace" {
		t.Fatalf("expected one replace op, got %+v", ops)
	}
	if ops[0].Path != "/shapes/0/fill" {
		t.Errorf("path = %s", ops[0].Path)
	}
}

func TestDiffOfIdenticalVersionsIsEmpty(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")
	scene := singleCube(t)

	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene})

	diff, err := s.Diff(p.ID, 1, 2)
	if err != nil {
		t.Fatal(err)
	}
	if diff.Changed {
		t.Error("identical scenes should not report a change")
	}
	var ops []patchOp
	json.Unmarshal(diff.Patch, &ops)
	if len(ops) != 0 {
		t.Errorf("expected empty patch, got %+v", ops)
	}
}

// TestDiffIsIdentityBasedNotPositional is the reason diffKeyedArray exists: a
// positional diff would claim every shape changed when one is inserted at the front.
func TestDiffIsIdentityBasedNotPositional(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")

	scene := singleCube(t)
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: scene})

	withPrefix := recolor(t, scene, "@palette.base")
	shapes := withPrefix["shapes"].([]any)
	newShape := map[string]any{
		"id": "prefix", "type": "cube",
		"at":   map[string]any{"x": 1.0, "y": 1.0, "z": 0.0},
		"fill": "@palette.accent",
	}
	withPrefix["shapes"] = append([]any{newShape}, shapes...)

	if _, err := s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: withPrefix}); err != nil {
		t.Fatal(err)
	}

	diff, err := s.Diff(p.ID, 1, 2)
	if err != nil {
		t.Fatal(err)
	}
	var ops []patchOp
	json.Unmarshal(diff.Patch, &ops)

	if len(ops) != 1 {
		t.Fatalf("inserting one shape should yield one op, got %d: %+v", len(ops), ops)
	}
	if ops[0].Op != "add" {
		t.Errorf("expected add, got %s", ops[0].Op)
	}
}

func TestDiffDetectsRemoval(t *testing.T) {
	s := testStore(t)
	p, _ := s.CreateProject("p", "")

	two := singleCube(t)
	two["shapes"] = append(two["shapes"].([]any), map[string]any{
		"id": "extra", "type": "cube",
		"at":   map[string]any{"x": 1.0, "y": 1.0, "z": 0.0},
		"fill": "@palette.accent",
	})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: two})
	s.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: singleCube(t)})

	diff, _ := s.Diff(p.ID, 1, 2)
	var ops []patchOp
	json.Unmarshal(diff.Patch, &ops)
	if len(ops) != 1 || ops[0].Op != "remove" {
		t.Fatalf("expected one remove, got %+v", ops)
	}
	if diff.Summary.Removed != 1 {
		t.Errorf("summary.Removed = %d", diff.Summary.Removed)
	}
}

func TestThemesSeededAndProtected(t *testing.T) {
	s := testStore(t)

	themes, err := s.ListThemes()
	if err != nil {
		t.Fatal(err)
	}
	if len(themes) < 5 {
		t.Fatalf("expected builtin themes to be seeded, got %d", len(themes))
	}
	if !themes[0].IsBuiltin {
		t.Error("builtins should sort first")
	}

	if _, err := s.SaveTheme("nord", ThemeDoc{Colors: map[string]string{"a": "#FFFFFF"}}, ""); err == nil {
		t.Error("builtin themes must not be overwritable")
	}

	nord, _ := s.GetThemeByName("nord")
	if err := s.DeleteTheme(nord.ID); err == nil {
		t.Error("builtin themes must not be deletable")
	}
}

func TestSeedingIsIdempotent(t *testing.T) {
	dir := t.TempDir()
	opts := Options{DBPath: filepath.Join(dir, "t.db"), ScenesDir: filepath.Join(dir, "s")}

	s1, err := Open(opts)
	if err != nil {
		t.Fatal(err)
	}
	before, _ := s1.ListThemes()
	s1.Close()

	s2, err := Open(opts)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()
	after, _ := s2.ListThemes()

	if len(before) != len(after) {
		t.Errorf("reopening duplicated themes: %d -> %d", len(before), len(after))
	}
}

func TestUserThemeRoundTrip(t *testing.T) {
	s := testStore(t)

	doc := ThemeDoc{Name: "brand", Colors: map[string]string{"primary": "#FF0000"}}
	saved, err := s.SaveTheme("brand", doc, "")
	if err != nil {
		t.Fatal(err)
	}
	if saved.IsBuiltin {
		t.Error("user theme marked builtin")
	}

	path := filepath.Join(s.ScenesDir(), "themes", "brand.theme.json")
	if _, err := os.Stat(path); err != nil {
		t.Errorf("theme file not written: %v", err)
	}

	// Saving again updates in place rather than creating a duplicate.
	doc.Colors["secondary"] = "#00FF00"
	if _, err := s.SaveTheme("brand", doc, ""); err != nil {
		t.Fatal(err)
	}
	themes, _ := s.ListThemes()
	count := 0
	for _, th := range themes {
		if th.Name == "brand" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("expected 1 'brand' theme, got %d", count)
	}

	if err := s.DeleteTheme(saved.ID); err != nil {
		t.Errorf("user theme should be deletable: %v", err)
	}
}

func TestImportThemeDoesNotShadowBuiltin(t *testing.T) {
	s := testStore(t)

	imported, err := s.ImportTheme([]byte(`{"name":"nord","colors":{"x":"#FFFFFF"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if imported.Name != "nord-imported" {
		t.Errorf("expected suffixed name, got %q", imported.Name)
	}

	builtin, _ := s.GetThemeByName("nord")
	if !builtin.IsBuiltin {
		t.Error("builtin nord was replaced")
	}
}

func TestImportRejectsMalformed(t *testing.T) {
	s := testStore(t)
	if _, err := s.ImportTheme([]byte(`{not json`)); err == nil {
		t.Error("expected error for malformed theme")
	}
	if _, err := s.ImportTheme([]byte(`{"colors":{}}`)); err == nil {
		t.Error("expected error for nameless theme")
	}
}

func TestDataSurvivesReopen(t *testing.T) {
	dir := t.TempDir()
	opts := Options{DBPath: filepath.Join(dir, "t.db"), ScenesDir: filepath.Join(dir, "s")}

	s1, _ := Open(opts)
	p, _ := s1.CreateProject("persistent", "")
	s1.SaveScene(SaveSceneOptions{ProjectID: p.ID, Scene: singleCube(t), ChangeSummary: "v1"})
	s1.Close()

	s2, err := Open(opts)
	if err != nil {
		t.Fatal(err)
	}
	defer s2.Close()

	current, err := s2.GetCurrentScene(p.ID)
	if err != nil {
		t.Fatalf("scene lost across restart: %v", err)
	}
	if current.ChangeSummary != "v1" {
		t.Errorf("summary = %q", current.ChangeSummary)
	}
}
