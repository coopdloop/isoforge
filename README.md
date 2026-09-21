# IsoForge

**Describe it. Watch it build. Ship the cube.**

A local-first CLI that generates isometric cube-art logos through conversation. Instead
of producing unpredictable pixels, an LLM edits a strict typed JSON scene graph — the
**IsoDSL** — which a deterministic engine renders to SVG, PNG and full app-icon bundles.

The result is a logo you can diff, version, revert and re-theme like source code.

```console
$ isoforge chat
› a logo for a CLI task runner: three cubes tumbling down like a waterfall,
  cyan fading to deep blue, floating with gaps between them

  Create Cascade logo: three cubes tumbling diagonally down from cyan to deep blue
  v1 · rewrote
  [isometric preview renders in your terminal]

› make the top cube glow

  v2 · patched
› /diff
  v1 → v2  +1 -0 ~0
    add      /effects/glow
```

That second turn is the point: "make the top cube glow" became a **one-operation patch**,
not a regenerated image. Nothing else about the design moved.

## Why

Generic AI image generators produce results you cannot edit, cannot version, and cannot
nudge. Hiring a designer for a repo logo is overkill. IsoForge treats a logo as a
structured document: every change is a validated, reviewable diff.

## Install

```bash
pipx install isoforge
export OPENROUTER_API_KEY=sk-or-...   # or ANTHROPIC_API_KEY, or run Ollama
isoforge doctor
```

The wheel bundles everything: the Go daemon, the web UI, and a static SVG rasterizer.
No system libraries, no Node, no Go toolchain.

From source (needs Python 3.11+, Go 1.25+, pnpm):

```bash
git clone https://github.com/coopdloop/isoforge && cd isoforge
make install
```

## Usage

```bash
isoforge chat                          # start designing
isoforge chat --resume                 # continue your most recent design
isoforge chat --continue logo.isoforge.json   # resume from a file
isoforge chat -p ollama -m qwen3:8b    # local, offline

isoforge history                       # list versions
isoforge diff v2 v3                    # see exactly what changed
isoforge revert 2                      # restore v2 (appends a new version)
isoforge export png --size 512
isoforge export icons                  # favicon.ico + .icns-ready bundle
isoforge theme save my-brand
```

In-chat: `/preview` `/history` `/diff` `/revert n` `/export png` `/json` `/themes` `/help`

## How it works

```
CLI ──┐
      ├──→ isoforged (Go, :4747) ──→ isoforge-py (:5001)
browser ─┘   gateway + store           agent + render engine
             SQLite + JSON files       LLM tool-calling, SVG/PNG
```

Two processes, both local, no accounts, no cloud. The gateway is the only entrypoint;
it fans live scene updates to the browser over WebSocket.

### The guarantee

Raw LLM text is **never** parsed as design data. The model's only channel is two
tool calls — `set_scene` and `patch_scene` — and every payload must pass JSON Schema
validation plus semantic rules before it is stored or rendered. Invalid output is fed
back to the model with precise error codes for up to two repair attempts; if it still
fails, your existing design is left untouched.

Rendering is deterministic by construction: the same scene always produces
byte-identical SVG. Paint order is derived from geometry (`x+y+z`, ties broken by `id`)
rather than array order, so adding a shape can never silently restack the others.

Three implementations must agree — the Python exporter, the Go validator, and the
TypeScript browser preview — so all three are pinned to shared fixtures: golden SVG
bytes, a canonical-hash file, and a 150-case geometry conformance suite. If the browser
preview ever drifted from the exporter, you would be designing against a lie; these
tests are what prevent that.

## The IsoDSL

```jsonc
{
  "isodsl_version": "1.0.0",
  "canvas": { "width": 512, "height": 512, "background": null },
  "grid":   { "w": 8, "d": 8, "h": 8, "cell": 32 },
  "palette": { "colors": { "base": "#7C5CFF", "accent": "#22D3EE" } },
  "shapes": [
    { "id": "core", "type": "cube", "at": {"x":0,"y":0,"z":0}, "fill": "@palette.base" }
  ]
}
```

Primitives: `cube`, `ramp`, `cylinder`, `plane`, `group`. Give a shape one `fill` and
the renderer derives its three faces by shifting lightness in **OKLab**, which keeps
saturated brand colors on-hue instead of turning them grey.

Full schema: [`schemas/isodsl/v1/isodsl.schema.json`](schemas/isodsl/v1/isodsl.schema.json)
· semantic rules: [`RULES.md`](schemas/isodsl/v1/RULES.md)

## Development

```bash
make build        # web bundle + Go daemon
make test         # 454 tests: Go, Python services, CLI, web conformance
make lint
make wheels       # release wheels for macOS/Linux x arm64/amd64
make golden       # regenerate render goldens (review the diff!)
make conformance  # regenerate the TS/Python geometry fixture
```

Implementation plan and architecture decisions: [`PLAN.md`](PLAN.md)

## Status

Feature-complete for v1: CLI, web workbench, deterministic exports, and installable
wheels for macOS and Linux on arm64 and amd64.

## License

MIT
