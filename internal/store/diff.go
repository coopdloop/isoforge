package store

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// Diff compares two versions of a project's scene and returns an RFC-6902 patch.
func (s *Store) Diff(projectID string, fromVersion, toVersion int) (*DiffResult, error) {
	from, err := s.GetSceneVersionByNumber(projectID, fromVersion)
	if err != nil {
		return nil, fmt.Errorf("from version %d: %w", fromVersion, err)
	}
	to, err := s.GetSceneVersionByNumber(projectID, toVersion)
	if err != nil {
		return nil, fmt.Errorf("to version %d: %w", toVersion, err)
	}

	var a, b any
	if err := json.Unmarshal(from.Scene, &a); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(to.Scene, &b); err != nil {
		return nil, err
	}

	ops := diffValue("", a, b)
	encoded, err := json.Marshal(ops)
	if err != nil {
		return nil, err
	}

	return &DiffResult{
		FromVersion: fromVersion,
		ToVersion:   toVersion,
		FromHash:    from.SceneHash,
		ToHash:      to.SceneHash,
		Changed:     from.SceneHash != to.SceneHash,
		Patch:       encoded,
		Summary:     summarise(ops),
	}, nil
}

// patchOp is one RFC-6902 operation.
type patchOp struct {
	Op    string `json:"op"`
	Path  string `json:"path"`
	Value any    `json:"value,omitempty"`
}

// diffValue produces a minimal patch between two decoded JSON values.
//
// Arrays are diffed by identity where elements carry an "id" (which every shape
// does), rather than positionally. A positional diff would report "every shape
// changed" when one shape is inserted at the front, which is useless in a UI whose
// whole job is showing what the model actually edited.
func diffValue(path string, a, b any) []patchOp {
	if sameJSON(a, b) {
		return nil
	}

	aMap, aIsMap := a.(map[string]any)
	bMap, bIsMap := b.(map[string]any)
	if aIsMap && bIsMap {
		return diffObject(path, aMap, bMap)
	}

	aArr, aIsArr := a.([]any)
	bArr, bIsArr := b.([]any)
	if aIsArr && bIsArr && keyedByID(aArr) && keyedByID(bArr) {
		return diffKeyedArray(path, aArr, bArr)
	}

	return []patchOp{{Op: "replace", Path: path, Value: b}}
}

func diffObject(path string, a, b map[string]any) []patchOp {
	var ops []patchOp

	keys := make([]string, 0, len(a)+len(b))
	seen := map[string]bool{}
	for k := range a {
		keys = append(keys, k)
		seen[k] = true
	}
	for k := range b {
		if !seen[k] {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys) // deterministic patch output

	for _, k := range keys {
		child := path + "/" + escapePointer(k)
		av, inA := a[k]
		bv, inB := b[k]
		switch {
		case inA && !inB:
			ops = append(ops, patchOp{Op: "remove", Path: child})
		case !inA && inB:
			ops = append(ops, patchOp{Op: "add", Path: child, Value: bv})
		default:
			ops = append(ops, diffValue(child, av, bv)...)
		}
	}
	return ops
}

// keyedByID reports whether every element is an object with a string id.
func keyedByID(arr []any) bool {
	for _, item := range arr {
		obj, ok := item.(map[string]any)
		if !ok {
			return false
		}
		if _, ok := obj["id"].(string); !ok {
			return false
		}
	}
	return true
}

func diffKeyedArray(path string, a, b []any) []patchOp {
	var ops []patchOp

	aIndex := map[string]any{}
	aOrder := []string{}
	for _, item := range a {
		id := item.(map[string]any)["id"].(string)
		aIndex[id] = item
		aOrder = append(aOrder, id)
	}
	bIndex := map[string]any{}
	bOrder := []string{}
	for _, item := range b {
		id := item.(map[string]any)["id"].(string)
		bIndex[id] = item
		bOrder = append(bOrder, id)
	}

	// Removals first, in reverse index order, so earlier indices stay valid as the
	// patch is applied sequentially.
	for i := len(aOrder) - 1; i >= 0; i-- {
		if _, kept := bIndex[aOrder[i]]; !kept {
			ops = append(ops, patchOp{Op: "remove", Path: fmt.Sprintf("%s/%d", path, i)})
		}
	}

	// Modifications to surviving elements, addressed by their position in the new array.
	for newIdx, id := range bOrder {
		av, existed := aIndex[id]
		if !existed {
			continue
		}
		ops = append(ops, diffValue(fmt.Sprintf("%s/%d", path, newIdx), av, bIndex[id])...)
	}

	// Additions last, appended in order.
	for _, id := range bOrder {
		if _, existed := aIndex[id]; !existed {
			ops = append(ops, patchOp{Op: "add", Path: path + "/-", Value: bIndex[id]})
		}
	}

	return ops
}

// escapePointer applies RFC-6901 escaping.
func escapePointer(key string) string {
	key = strings.ReplaceAll(key, "~", "~0")
	return strings.ReplaceAll(key, "/", "~1")
}

func sameJSON(a, b any) bool {
	ea, errA := json.Marshal(a)
	eb, errB := json.Marshal(b)
	if errA != nil || errB != nil {
		return false
	}
	return string(ea) == string(eb)
}

func summarise(ops []patchOp) DiffSummary {
	s := DiffSummary{Paths: []string{}}
	for _, op := range ops {
		switch op.Op {
		case "add":
			s.Added++
		case "remove":
			s.Removed++
		case "replace":
			s.Replaced++
		case "move", "copy":
			s.Moved++
		}
		s.Paths = append(s.Paths, op.Path)
	}
	return s
}
