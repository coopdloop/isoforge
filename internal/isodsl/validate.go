package isodsl

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

var (
	paletteRefRe = regexp.MustCompile(`^@palette\.([a-z][a-zA-Z0-9_]{0,31})$`)
	literalRe    = regexp.MustCompile(`^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$`)
)

// planeNormal maps a plane orientation to the axis that must stay flat (rule ISO013).
var planeNormal = map[string]string{"top": "z", "left": "y", "right": "x"}

// Validate checks a scene against the schema and then the semantic rules.
//
// Schema errors short-circuit: the semantic checks assume well-formed structure, and
// reporting "shape 3 is out of bounds" on a document that isn't a valid shape at all
// would be noise.
func Validate(scene any) Result {
	obj, ok := scene.(map[string]any)
	if !ok {
		return Result{Valid: false, Errors: []Error{
			newError(CodeSchema, "", "", "scene must be a JSON object, got %T", scene),
		}}
	}

	s, err := schema()
	if err != nil {
		return Result{Valid: false, Errors: []Error{
			newError(CodeSchema, "", "", "schema unavailable: %v", err),
		}}
	}

	if err := s.Validate(scene); err != nil {
		if ve, ok := err.(*jsonschema.ValidationError); ok {
			return Result{Valid: false, Errors: schemaErrors(ve, obj)}
		}
		return Result{Valid: false, Errors: []Error{
			newError(CodeSchema, "", "", "%v", err),
		}}
	}

	errs := semanticErrors(obj)
	return Result{Valid: len(errs) == 0, Errors: errs}
}

// ValidateBytes validates raw JSON, reporting malformed input as a schema error.
func ValidateBytes(data []byte) Result {
	var scene any
	if err := unmarshalJSON(data, &scene); err != nil {
		return Result{Valid: false, Errors: []Error{
			newError(CodeSchema, "", "", "invalid JSON: %v", err),
		}}
	}
	return Validate(scene)
}

// schemaErrors flattens the validation tree into leaf causes, which name the actual
// problem rather than the enclosing "doesn't match oneOf" wrapper.
func schemaErrors(ve *jsonschema.ValidationError, scene map[string]any) []Error {
	var out []Error
	var walk func(e *jsonschema.ValidationError)
	walk = func(e *jsonschema.ValidationError) {
		if len(e.Causes) == 0 {
			path := formatInstancePath(e.InstanceLocation)
			out = append(out, newError(CodeSchema, path, shapeIDAt(scene, e.InstanceLocation),
				"%s", e.ErrorKind.LocalizedString(defaultPrinter)))
			return
		}
		for _, c := range e.Causes {
			walk(c)
		}
	}
	walk(ve)

	if len(out) == 0 {
		out = append(out, newError(CodeSchema, "", "", "%s",
			ve.ErrorKind.LocalizedString(defaultPrinter)))
	}
	// Deterministic order so identical input always yields identical output.
	sort.SliceStable(out, func(i, j int) bool { return out[i].Path < out[j].Path })
	return out
}

func formatInstancePath(parts []string) string {
	var b strings.Builder
	for _, p := range parts {
		if isIndex(p) {
			fmt.Fprintf(&b, "[%s]", p)
			continue
		}
		if b.Len() > 0 {
			b.WriteByte('.')
		}
		b.WriteString(p)
	}
	return b.String()
}

func isIndex(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// shapeIDAt resolves the enclosing shape's id so the UI can highlight it.
func shapeIDAt(scene map[string]any, path []string) string {
	var cur any = scene
	best := ""
	for _, p := range path {
		switch node := cur.(type) {
		case map[string]any:
			if id, ok := node["id"].(string); ok {
				best = id
			}
			cur = node[p]
		case []any:
			idx := 0
			if _, err := fmt.Sscanf(p, "%d", &idx); err != nil || idx >= len(node) {
				return best
			}
			cur = node[idx]
		default:
			return best
		}
	}
	if node, ok := cur.(map[string]any); ok {
		if id, ok := node["id"].(string); ok {
			best = id
		}
	}
	return best
}

type flatShape struct {
	shape  map[string]any
	origin [3]int
	depth  int
	path   string
}

// walkShapes yields every shape with group offsets resolved into absolute coordinates.
func walkShapes(shapes []any, origin [3]int, depth int, prefix string, fn func(flatShape)) {
	for i, raw := range shapes {
		shape, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		path := fmt.Sprintf("%s[%d]", prefix, i)
		at, _ := shape["at"].(map[string]any)
		abs := [3]int{
			origin[0] + intField(at, "x"),
			origin[1] + intField(at, "y"),
			origin[2] + intField(at, "z"),
		}
		fn(flatShape{shape: shape, origin: abs, depth: depth, path: path})

		if shape["type"] == "group" {
			if children, ok := shape["children"].([]any); ok {
				walkShapes(children, abs, depth+1, path+".children", fn)
			}
		}
	}
}

func semanticErrors(scene map[string]any) []Error {
	var errs []Error

	shapes, _ := scene["shapes"].([]any)
	grid, _ := scene["grid"].(map[string]any)
	palette, _ := scene["palette"].(map[string]any)
	colors, _ := palette["colors"].(map[string]any)
	locked, _ := palette["locked"].(bool)

	gw, gd, gh := intField(grid, "w"), intField(grid, "d"), intField(grid, "h")

	seen := map[string]string{}
	walkShapes(shapes, [3]int{}, 0, "shapes", func(fs flatShape) {
		id, _ := fs.shape["id"].(string)
		stype, _ := fs.shape["type"].(string)

		// ISO001: unique ids across the flattened tree.
		if id != "" {
			if prev, dup := seen[id]; dup {
				errs = append(errs, newError(CodeDuplicateID, fs.path+".id", id,
					"id '%s' is already used at %s", id, prev))
			} else {
				seen[id] = fs.path
			}
		}

		// ISO012: bounded nesting.
		if fs.depth >= MaxGroupDepth {
			errs = append(errs, newError(CodeNestingTooDeep, fs.path, id,
				"group nesting depth %d exceeds maximum %d", fs.depth+1, MaxGroupDepth))
		}

		// Groups are pure offsets and may legitimately sit on the far edge; only
		// leaf shapes occupy space and get bounds-checked.
		if stype == "group" {
			return
		}

		x, y, z := fs.origin[0], fs.origin[1], fs.origin[2]
		if x < 0 || x >= gw || y < 0 || y >= gd || z < 0 || z >= gh {
			errs = append(errs, newError(CodeOutOfBounds, fs.path+".at", id,
				"absolute origin (%d, %d, %d) is outside grid %dx%dx%d", x, y, z, gw, gd, gh))
		} else {
			// ISO011: extent within the grid.
			size, _ := fs.shape["size"].(map[string]any)
			for _, axis := range []struct {
				name  string
				orig  int
				limit int
			}{{"x", x, gw}, {"y", y, gd}, {"z", z, gh}} {
				extent := float64(axis.orig) + floatFieldDefault(size, axis.name, 1)
				if extent > float64(axis.limit) {
					errs = append(errs, newError(CodeExtentOverflow,
						fs.path+".size."+axis.name, id,
						"extent on %s reaches %g, exceeding grid limit %d",
						axis.name, extent, axis.limit))
				}
			}
		}

		// ISO013: planes are flat on their normal axis.
		if stype == "plane" {
			orientation, _ := fs.shape["orientation"].(string)
			if normal, ok := planeNormal[orientation]; ok {
				size, _ := fs.shape["size"].(map[string]any)
				if v := floatFieldDefault(size, normal, 0); v != 0 {
					errs = append(errs, newError(CodePlaneThickness,
						fs.path+".size."+normal, id,
						"plane with orientation '%s' must have zero extent on %s, got %g",
						orientation, normal, v))
				}
			}
		}
	})

	// ISO002 / ISO003: colors across shapes and effects.
	for _, root := range []struct {
		node  any
		label string
	}{{scene["shapes"], "shapes"}, {scene["effects"], "effects"}} {
		if root.node == nil {
			continue
		}
		walkColors(root.node, root.label, func(color, path string) {
			if m := paletteRefRe.FindStringSubmatch(color); m != nil {
				if _, ok := colors[m[1]]; !ok {
					errs = append(errs, newError(CodeUnknownPaletteRef, path, "",
						"'%s' does not resolve; palette defines: %s", color, knownKeys(colors)))
				}
				return
			}
			if locked && literalRe.MatchString(color) {
				errs = append(errs, newError(CodeLiteralUnderLock, path, "",
					"literal color '%s' is not allowed while the palette is locked", color))
			}
		})
	}

	return errs
}

func walkColors(node any, path string, fn func(color, path string)) {
	switch v := node.(type) {
	case map[string]any:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys) // deterministic error ordering
		for _, k := range keys {
			sub := k
			if path != "" {
				sub = path + "." + k
			}
			if s, ok := v[k].(string); ok && (paletteRefRe.MatchString(s) || literalRe.MatchString(s)) {
				fn(s, sub)
				continue
			}
			walkColors(v[k], sub, fn)
		}
	case []any:
		for i, item := range v {
			walkColors(item, fmt.Sprintf("%s[%d]", path, i), fn)
		}
	}
}

func knownKeys(colors map[string]any) string {
	if len(colors) == 0 {
		return "(none)"
	}
	keys := make([]string, 0, len(colors))
	for k := range colors {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return strings.Join(keys, ", ")
}

func intField(m map[string]any, key string) int {
	if m == nil {
		return 0
	}
	if f, ok := m[key].(float64); ok {
		return int(f)
	}
	return 0
}

func floatFieldDefault(m map[string]any, key string, def float64) float64 {
	if m == nil {
		return def
	}
	if f, ok := m[key].(float64); ok {
		return f
	}
	return def
}
