// Package isodsl validates IsoDSL v1 scene documents.
//
// This is the Go mirror of services/isoforge_py/isodsl. Both implementations are
// pinned to the same fixture suite in testdata/scenes so they can never drift:
// a scene accepted here must be accepted there, with the same error codes.
package isodsl

import (
	"fmt"
	"strings"
)

// ErrorCode is a stable public contract. The agent repair loop feeds these back to
// the LLM and the web UI renders them, so never renumber one.
type ErrorCode string

const (
	CodeSchema            ErrorCode = "SCHEMA"
	CodeDuplicateID       ErrorCode = "ISO001"
	CodeUnknownPaletteRef ErrorCode = "ISO002"
	CodeLiteralUnderLock  ErrorCode = "ISO003"
	CodeOutOfBounds       ErrorCode = "ISO010"
	CodeExtentOverflow    ErrorCode = "ISO011"
	CodeNestingTooDeep    ErrorCode = "ISO012"
	CodePlaneThickness    ErrorCode = "ISO013"
)

// MaxGroupDepth bounds group nesting (rule ISO012).
const MaxGroupDepth = 8

// remedies mirror REMEDY in the Python implementation. Phrase each as an actionable
// instruction, not a description of the failure.
var remedies = map[ErrorCode]string{
	CodeSchema: "Emit only properties defined in the IsoDSL v1 schema. Unknown properties are " +
		"rejected outright; there is no passthrough.",
	CodeDuplicateID: "Give every shape a unique id across the whole tree, including shapes nested " +
		"in groups. Rename the duplicate rather than removing either shape.",
	CodeUnknownPaletteRef: "Reference only keys that exist in palette.colors, or add the missing " +
		"key to the palette in the same edit.",
	CodeLiteralUnderLock: "The palette is locked. Replace literal hex colors with '@palette.<name>' " +
		"references, or unlock the palette first if the user asked for a new color.",
	CodeOutOfBounds: "Place the shape inside the grid. Remember that a shape inside a group is " +
		"offset by that group's 'at' as well as its own.",
	CodeExtentOverflow: "Shrink the shape's size or move its origin so that origin + size stays " +
		"within the grid, or enlarge the grid to fit.",
	CodeNestingTooDeep: "Flatten the structure; groups may nest at most 8 deep.",
	CodePlaneThickness: "A plane is a flat quad. Omit the size component on its normal axis, or " +
		"set it to 0.",
}

// Error is one validation failure addressed to a precise location in the document.
type Error struct {
	Code    ErrorCode `json:"code"`
	Message string    `json:"message"`
	Path    string    `json:"path,omitempty"`
	ShapeID string    `json:"shape_id,omitempty"`
	Remedy  string    `json:"remedy,omitempty"`
}

func newError(code ErrorCode, path, shapeID, format string, args ...any) Error {
	return Error{
		Code:    code,
		Message: fmt.Sprintf(format, args...),
		Path:    path,
		ShapeID: shapeID,
		Remedy:  remedies[code],
	}
}

func (e Error) String() string {
	if e.Path != "" {
		return fmt.Sprintf("[%s] at %s: %s", e.Code, e.Path, e.Message)
	}
	return fmt.Sprintf("[%s]: %s", e.Code, e.Message)
}

// Result is the outcome of validating one document.
type Result struct {
	Valid  bool    `json:"valid"`
	Errors []Error `json:"errors"`
}

// Err returns a non-nil error when the document is invalid, for call sites that
// prefer idiomatic error handling over inspecting Result.
func (r Result) Err() error {
	if r.Valid {
		return nil
	}
	return &ValidationError{Result: r}
}

// AsPrompt renders the errors as repair instructions for the LLM.
func (r Result) AsPrompt() string {
	var b strings.Builder
	for _, e := range r.Errors {
		fmt.Fprintf(&b, "- %s\n", e.String())
		if e.Remedy != "" {
			fmt.Fprintf(&b, "  fix: %s\n", e.Remedy)
		}
	}
	return b.String()
}

// ValidationError adapts Result to the error interface.
type ValidationError struct {
	Result Result
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("%d IsoDSL validation error(s)\n%s", len(e.Result.Errors), e.Result.AsPrompt())
}
