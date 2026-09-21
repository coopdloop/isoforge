package isodsl

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// repoRoot walks up from the test's working directory to the module root.
func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 8; i++ {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir
		}
		dir = filepath.Dir(dir)
	}
	t.Fatal("could not locate repo root")
	return ""
}

func loadScene(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var scene map[string]any
	if err := json.Unmarshal(data, &scene); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	return scene
}

func globScenes(t *testing.T, sub string) []string {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(repoRoot(t), "testdata", "scenes", sub, "*.isoforge.json"))
	if err != nil || len(paths) == 0 {
		t.Fatalf("no fixtures found in %s (err=%v)", sub, err)
	}
	return paths
}

// TestValidFixturesPass is half of the cross-language contract: every fixture the
// Python validator accepts must be accepted here too.
func TestValidFixturesPass(t *testing.T) {
	for _, path := range globScenes(t, "valid") {
		t.Run(filepath.Base(path), func(t *testing.T) {
			result := Validate(loadScene(t, path))
			if !result.Valid {
				t.Fatalf("expected valid, got errors:\n%s", result.AsPrompt())
			}
		})
	}
}

// TestInvalidFixturesReportExpectedCode is the other half: the same fixture must
// produce the same ISO code in both languages.
func TestInvalidFixturesReportExpectedCode(t *testing.T) {
	for _, path := range globScenes(t, "invalid") {
		t.Run(filepath.Base(path), func(t *testing.T) {
			scene := loadScene(t, path)
			expected, _ := scene["_expect_error"].(string)
			if expected == "" {
				t.Fatal("fixture is missing _expect_error")
			}
			delete(scene, "_expect_error")
			delete(scene, "_note")

			result := Validate(scene)
			if result.Valid {
				t.Fatalf("expected %s, but document validated cleanly", expected)
			}
			for _, e := range result.Errors {
				if string(e.Code) == expected {
					return
				}
			}
			var got []string
			for _, e := range result.Errors {
				got = append(got, string(e.Code))
			}
			t.Fatalf("expected %s, got %v", expected, got)
		})
	}
}

func TestEveryCodeHasRemedy(t *testing.T) {
	codes := []ErrorCode{
		CodeSchema, CodeDuplicateID, CodeUnknownPaletteRef, CodeLiteralUnderLock,
		CodeOutOfBounds, CodeExtentOverflow, CodeNestingTooDeep, CodePlaneThickness,
	}
	for _, c := range codes {
		if remedies[c] == "" {
			t.Errorf("%s has no remedy text", c)
		}
	}
}

func TestRejectsNonObject(t *testing.T) {
	for _, input := range []any{nil, "scene", 42, []any{}} {
		if Validate(input).Valid {
			t.Errorf("expected %v to be rejected", input)
		}
	}
}

func TestValidateBytesReportsMalformedJSON(t *testing.T) {
	result := ValidateBytes([]byte("{not json"))
	if result.Valid {
		t.Fatal("expected malformed JSON to be rejected")
	}
	if result.Errors[0].Code != CodeSchema {
		t.Errorf("expected SCHEMA, got %s", result.Errors[0].Code)
	}
}

func TestUnknownPropertyRejected(t *testing.T) {
	base := loadScene(t, filepath.Join(repoRoot(t), "testdata", "scenes", "valid", "01-single-cube.isoforge.json"))
	shapes := base["shapes"].([]any)
	shapes[0].(map[string]any)["rotation"] = 45.0

	result := Validate(base)
	if result.Valid {
		t.Fatal("additionalProperties:false must reject unknown keys")
	}
}

func TestUnknownPaletteRefNamesKnownKeys(t *testing.T) {
	base := loadScene(t, filepath.Join(repoRoot(t), "testdata", "scenes", "valid", "01-single-cube.isoforge.json"))
	shapes := base["shapes"].([]any)
	shapes[0].(map[string]any)["fill"] = "@palette.ghost"

	result := Validate(base)
	for _, e := range result.Errors {
		if e.Code == CodeUnknownPaletteRef {
			if !strings.Contains(e.Message, "base") || !strings.Contains(e.Message, "accent") {
				t.Errorf("error should name the valid options, got: %s", e.Message)
			}
			return
		}
	}
	t.Fatal("expected ISO002")
}

func TestGroupOffsetAccumulatesForBounds(t *testing.T) {
	scene := map[string]any{
		"isodsl_version": "1.0.0",
		"canvas":         map[string]any{"width": 512.0, "height": 512.0},
		"grid":           map[string]any{"w": 4.0, "d": 4.0, "h": 4.0},
		"palette":        map[string]any{"colors": map[string]any{"base": "#7C5CFF"}},
		"shapes": []any{
			map[string]any{
				"id": "g", "type": "group",
				"at": map[string]any{"x": 3.0, "y": 3.0, "z": 0.0},
				"children": []any{
					map[string]any{
						"id": "far", "type": "cube",
						"at":   map[string]any{"x": 3.0, "y": 3.0, "z": 0.0},
						"fill": "@palette.base",
					},
				},
			},
		},
	}
	result := Validate(scene)
	for _, e := range result.Errors {
		if e.Code == CodeOutOfBounds {
			return
		}
	}
	t.Fatalf("expected ISO010, got %s", result.AsPrompt())
}

func TestErrorsAreDeterministicallyOrdered(t *testing.T) {
	base := loadScene(t, filepath.Join(repoRoot(t), "testdata", "scenes", "valid", "03-all-primitives.isoforge.json"))
	shapes := base["shapes"].([]any)
	for _, s := range shapes {
		s.(map[string]any)["fill"] = "@palette.ghost"
	}

	first := Validate(base)
	for i := 0; i < 5; i++ {
		next := Validate(base)
		if len(next.Errors) != len(first.Errors) {
			t.Fatal("error count varies between runs")
		}
		for j := range next.Errors {
			if next.Errors[j].Path != first.Errors[j].Path {
				t.Fatalf("error order varies between runs at %d", j)
			}
		}
	}
}

func TestSchemaIsServable(t *testing.T) {
	s, err := Schema()
	if err != nil {
		t.Fatal(err)
	}
	if s["title"] != "IsoDSL Scene" {
		t.Errorf("unexpected schema title: %v", s["title"])
	}
}
