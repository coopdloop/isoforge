package isodsl

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// Precision is the fixed number of decimal places for emitted floats (rule ISO021).
const Precision = 4

// keyOrder mirrors _KEY_ORDER in the Python canonical serializer (rule ISO022).
// Keys absent from this list sort last, alphabetically, so forward-compatible
// additions degrade predictably rather than scrambling output.
var keyOrder = buildKeyOrder([]string{
	"isodsl_version", "meta", "canvas", "grid", "camera", "palette",
	"shading", "effects", "shapes",
	"name", "description", "tags",
	"width", "height", "background", "padding",
	"w", "d", "h", "cell", "show",
	"projection", "fit",
	"id", "locked", "colors",
	"enabled", "top", "left", "right",
	"outline", "shadow", "glow",
	"color", "opacity", "blur", "offset", "radius", "intensity", "scope",
	"type", "at", "size", "facing", "orientation", "segments",
	"fill", "faces", "stroke", "strokeWidth", "bevel",
	"visible", "label", "children",
	"x", "y", "z",
})

func buildKeyOrder(keys []string) map[string]int {
	m := make(map[string]int, len(keys))
	for i, k := range keys {
		m[k] = i
	}
	return m
}

func keyRank(key string) (int, string) {
	if rank, ok := keyOrder[key]; ok {
		return rank, key
	}
	return len(keyOrder), key
}

// FormatFloat renders a number with fixed precision, no exponent and no negative zero.
//
// Go's strconv defaults would emit "1e+12" for large values and "-0" for negative
// zero; both would break byte-stability against the Python renderer.
func FormatFloat(v float64) string {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return "0.0"
	}
	rounded := roundHalfUp(v, Precision)
	if rounded == 0 {
		rounded = 0 // collapse -0
	}
	s := strconv.FormatFloat(rounded, 'f', Precision, 64)
	if strings.Contains(s, ".") {
		s = strings.TrimRight(s, "0")
		if strings.HasSuffix(s, ".") {
			s += "0"
		}
	}
	return s
}

// formatJSONNumber preserves integer-valued numbers as integers.
//
// encoding/json decodes every number as float64, which would render 512 as "512.0".
// Python's json keeps 512 an int, so emitting the float form here would silently
// break hash agreement between the two implementations.
func formatJSONNumber(v float64) string {
	if v == math.Trunc(v) && !math.IsInf(v, 0) && math.Abs(v) < 1e15 {
		return strconv.FormatInt(int64(v), 10)
	}
	return FormatFloat(v)
}

// roundHalfUp matches Python's Decimal ROUND_HALF_UP, which differs from Go's
// banker's rounding on exact .5 boundaries.
func roundHalfUp(v float64, places int) float64 {
	shift := math.Pow(10, float64(places))
	scaled := v * shift
	if scaled < 0 {
		return -math.Floor(-scaled+0.5) / shift
	}
	return math.Floor(scaled+0.5) / shift
}

// CanonicalJSON serializes a scene in canonical form: schema key order, fixed float
// precision, and fixture annotations stripped.
//
// indent < 0 produces the compact form used for hashing and the WebSocket wire;
// indent >= 0 produces the on-disk form.
func CanonicalJSON(scene any, indent int) ([]byte, error) {
	var buf bytes.Buffer
	if err := writeCanonical(&buf, scene, indent, 0); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func writeCanonical(buf *bytes.Buffer, node any, indent, depth int) error {
	switch v := node.(type) {
	case map[string]any:
		return writeObject(buf, v, indent, depth)
	case []any:
		return writeArray(buf, v, indent, depth)
	case string:
		encoded, err := json.Marshal(v)
		if err != nil {
			return err
		}
		buf.Write(encoded)
	case bool:
		buf.WriteString(strconv.FormatBool(v))
	case float64:
		buf.WriteString(formatJSONNumber(v))
	case int:
		buf.WriteString(strconv.Itoa(v))
	case json.Number:
		f, err := v.Float64()
		if err != nil {
			return err
		}
		buf.WriteString(formatJSONNumber(f))
	case nil:
		buf.WriteString("null")
	default:
		encoded, err := json.Marshal(v)
		if err != nil {
			return err
		}
		buf.Write(encoded)
	}
	return nil
}

func writeObject(buf *bytes.Buffer, obj map[string]any, indent, depth int) error {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		if strings.HasPrefix(k, "_") {
			continue // drop fixture annotations
		}
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		ri, ki := keyRank(keys[i])
		rj, kj := keyRank(keys[j])
		if ri != rj {
			return ri < rj
		}
		return ki < kj
	})

	if len(keys) == 0 {
		buf.WriteString("{}")
		return nil
	}

	buf.WriteByte('{')
	for i, k := range keys {
		if i > 0 {
			buf.WriteByte(',')
		}
		writeNewlineIndent(buf, indent, depth+1)
		encoded, err := json.Marshal(k)
		if err != nil {
			return err
		}
		buf.Write(encoded)
		buf.WriteByte(':')
		if indent >= 0 {
			buf.WriteByte(' ')
		}
		if err := writeCanonical(buf, obj[k], indent, depth+1); err != nil {
			return err
		}
	}
	writeNewlineIndent(buf, indent, depth)
	buf.WriteByte('}')
	return nil
}

func writeArray(buf *bytes.Buffer, arr []any, indent, depth int) error {
	if len(arr) == 0 {
		buf.WriteString("[]")
		return nil
	}
	buf.WriteByte('[')
	for i, item := range arr {
		if i > 0 {
			buf.WriteByte(',')
		}
		writeNewlineIndent(buf, indent, depth+1)
		if err := writeCanonical(buf, item, indent, depth+1); err != nil {
			return err
		}
	}
	writeNewlineIndent(buf, indent, depth)
	buf.WriteByte(']')
	return nil
}

func writeNewlineIndent(buf *bytes.Buffer, indent, depth int) {
	if indent < 0 {
		return
	}
	buf.WriteByte('\n')
	buf.WriteString(strings.Repeat(" ", indent*depth))
}

// SceneHash is the stable content hash of a scene. Cosmetic reformatting does not
// change it; a design change always does.
func SceneHash(scene any) (string, error) {
	canonical, err := CanonicalJSON(scene, -1)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

// SceneHashBytes hashes raw scene JSON after canonicalising it.
func SceneHashBytes(data []byte) (string, error) {
	var scene any
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&scene); err != nil {
		return "", fmt.Errorf("invalid scene JSON: %w", err)
	}
	return SceneHash(normalizeNumbers(scene))
}

// normalizeNumbers converts json.Number to float64 so hashing is independent of how
// the document was decoded.
func normalizeNumbers(node any) any {
	switch v := node.(type) {
	case map[string]any:
		out := make(map[string]any, len(v))
		for k, item := range v {
			out[k] = normalizeNumbers(item)
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = normalizeNumbers(item)
		}
		return out
	case json.Number:
		f, err := v.Float64()
		if err != nil {
			return v.String()
		}
		return f
	default:
		return node
	}
}
