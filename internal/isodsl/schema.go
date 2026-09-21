package isodsl

import (
	"bytes"
	"embed"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// SchemaVersion is the IsoDSL revision this package implements.
const SchemaVersion = "1.0.0"

//go:embed isodsl.schema.json
var embeddedSchema embed.FS

var (
	compileOnce sync.Once
	compiled    *jsonschema.Schema
	compileErr  error
	rawSchema   map[string]any
)

// schemaBytes resolves the schema, preferring an explicit override so that a dev
// running against a modified schema does not silently validate against the embedded
// copy baked in at build time.
func schemaBytes() ([]byte, error) {
	if p := os.Getenv("ISODSL_SCHEMA_PATH"); p != "" {
		data, err := os.ReadFile(p)
		if err != nil {
			return nil, fmt.Errorf("ISODSL_SCHEMA_PATH=%s: %w", p, err)
		}
		return data, nil
	}
	if data, err := embeddedSchema.ReadFile("isodsl.schema.json"); err == nil {
		return data, nil
	}
	// Development fallback: walk up to the repo root.
	dir, err := os.Getwd()
	if err != nil {
		return nil, err
	}
	for i := 0; i < 8; i++ {
		candidate := filepath.Join(dir, "schemas", "isodsl", "v1", "isodsl.schema.json")
		if data, err := os.ReadFile(candidate); err == nil {
			return data, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return nil, fmt.Errorf("could not locate isodsl.schema.json; set ISODSL_SCHEMA_PATH")
}

func schema() (*jsonschema.Schema, error) {
	compileOnce.Do(func() {
		data, err := schemaBytes()
		if err != nil {
			compileErr = err
			return
		}
		if err := json.Unmarshal(data, &rawSchema); err != nil {
			compileErr = fmt.Errorf("schema is not valid JSON: %w", err)
			return
		}

		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
		if err != nil {
			compileErr = err
			return
		}
		c := jsonschema.NewCompiler()
		const url = "https://isoforge.dev/schemas/isodsl/v1/isodsl.schema.json"
		if err := c.AddResource(url, doc); err != nil {
			compileErr = err
			return
		}
		compiled, compileErr = c.Compile(url)
	})
	return compiled, compileErr
}

// Schema returns the raw schema document, for serving to clients.
func Schema() (map[string]any, error) {
	if _, err := schema(); err != nil {
		return nil, err
	}
	return rawSchema, nil
}
