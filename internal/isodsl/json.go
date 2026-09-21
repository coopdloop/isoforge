package isodsl

import (
	"encoding/json"

	"golang.org/x/text/language"
	"golang.org/x/text/message"
)

// defaultPrinter localises schema error messages. English is the only catalogue we
// ship; the library requires a printer regardless.
var defaultPrinter = message.NewPrinter(language.English)

func unmarshalJSON(data []byte, v any) error {
	return json.Unmarshal(data, v)
}
