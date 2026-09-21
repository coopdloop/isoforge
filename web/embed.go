// Package web embeds the built React preview UI.
//
// The bundle is optional: a development build has no dist/ directory, and the CLI can
// run against a Vite dev server instead. FS reports that case as an error rather than
// failing the build, so `go build ./...` works on a fresh clone.
package web

import (
	"embed"
	"errors"
	"io/fs"
)

// ErrNoBundle indicates the web UI was not built into this binary.
var ErrNoBundle = errors.New("no embedded web bundle")

//go:embed all:dist
var dist embed.FS

// FS returns the built bundle rooted at dist/.
func FS() (fs.FS, error) {
	sub, err := fs.Sub(dist, "dist")
	if err != nil {
		return nil, ErrNoBundle
	}
	// A placeholder-only dist means nothing real was built.
	if _, err := fs.Stat(sub, "index.html"); err != nil {
		return nil, ErrNoBundle
	}
	return sub, nil
}
