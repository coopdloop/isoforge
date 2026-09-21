package isodsl

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestSceneHashesMatchGoldenFile is the cross-language determinism contract.
//
// The golden file is generated from the Python implementation. If Go and Python ever
// disagree about canonical form, this fails, because a scene stored by the Go service
// would otherwise hash differently from the same scene rendered by Python, silently
// breaking version dedup and diffing.
func TestSceneHashesMatchGoldenFile(t *testing.T) {
	root := repoRoot(t)
	goldenPath := filepath.Join(root, "testdata", "golden", "scene-hashes.txt")

	file, err := os.Open(goldenPath)
	if err != nil {
		t.Fatalf("open golden hashes: %v", err)
	}
	defer file.Close()

	count := 0
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) != 2 {
			t.Fatalf("malformed golden line: %q", line)
		}
		wantHash, name := parts[0], parts[1]

		t.Run(name, func(t *testing.T) {
			scene := loadScene(t, filepath.Join(root, "testdata", "scenes", "valid", name))
			got, err := SceneHash(scene)
			if err != nil {
				t.Fatal(err)
			}
			if got != wantHash {
				t.Errorf("canonical form drifted from Python\n got: %s\nwant: %s", got, wantHash)
			}
		})
		count++
	}
	if count == 0 {
		t.Fatal("golden hash file is empty")
	}
}

func TestFormatFloat(t *testing.T) {
	cases := []struct {
		in   float64
		want string
	}{
		{0, "0.0"},
		{-0.0, "0.0"},
		{1, "1.0"},
		{0.5, "0.5"},
		{1e-9, "0.0"},
		{0.1 + 0.2, "0.3"},
		{1234.56789, "1234.5679"},
		{-27.712812921102035, "-27.7128"},
		{0.00005, "0.0001"}, // half-up, not banker's rounding
	}
	for _, c := range cases {
		if got := FormatFloat(c.in); got != c.want {
			t.Errorf("FormatFloat(%v) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestFormatFloatNeverUsesExponent(t *testing.T) {
	for _, v := range []float64{1e-20, 1e20, -1e-15} {
		if strings.ContainsAny(FormatFloat(v), "eE") {
			t.Errorf("FormatFloat(%v) used exponent notation: %s", v, FormatFloat(v))
		}
	}
}

// TestIntegersStayIntegers guards the exact bug this contract caught during
// development: encoding/json decodes every number as float64, so 512 would render
// as "512.0" and diverge from Python.
func TestIntegersStayIntegers(t *testing.T) {
	scene := map[string]any{"canvas": map[string]any{"width": float64(512)}}
	out, err := CanonicalJSON(scene, -1)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(out), `"width":512`) {
		t.Errorf("integer rendered as float: %s", out)
	}
}

func TestCanonicalIgnoresKeyOrder(t *testing.T) {
	a := []byte(`{"grid":{"w":4,"d":4,"h":4},"isodsl_version":"1.0.0"}`)
	b := []byte(`{"isodsl_version":"1.0.0","grid":{"h":4,"d":4,"w":4}}`)

	var sa, sb any
	json.Unmarshal(a, &sa)
	json.Unmarshal(b, &sb)

	ha, err := SceneHash(sa)
	if err != nil {
		t.Fatal(err)
	}
	hb, err := SceneHash(sb)
	if err != nil {
		t.Fatal(err)
	}
	if ha != hb {
		t.Error("key order changed the hash")
	}
}

func TestCanonicalStripsAnnotations(t *testing.T) {
	scene := map[string]any{"_expect_error": "ISO001", "isodsl_version": "1.0.0"}
	out, err := CanonicalJSON(scene, -1)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(out), "_expect_error") {
		t.Errorf("fixture annotation leaked into canonical form: %s", out)
	}
}

func TestCanonicalIndentedFormIsStable(t *testing.T) {
	scene := loadScene(t, filepath.Join(repoRoot(t), "testdata", "scenes", "valid", "01-single-cube.isoforge.json"))
	first, err := CanonicalJSON(scene, 2)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 5; i++ {
		next, _ := CanonicalJSON(scene, 2)
		if string(next) != string(first) {
			t.Fatal("indented canonical form varies between runs")
		}
	}
	if !strings.Contains(string(first), "\n  \"isodsl_version\"") {
		t.Errorf("unexpected indentation:\n%s", first[:80])
	}
}

func TestSceneHashBytesRejectsMalformed(t *testing.T) {
	if _, err := SceneHashBytes([]byte("{not json")); err == nil {
		t.Error("expected error for malformed JSON")
	}
}
