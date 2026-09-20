# IsoForge — Implementation Plan

> Source spec: `coopdloop/a-llm-cli-chat-where-you-talk-with-and-i-spec` (`product.json`, theorycraft v1.0.0)

Conversational CLI that generates isometric cube-art logos by having an LLM edit a
strict typed JSON scene graph (**IsoDSL**), deterministically rendered to SVG/PNG/icon
bundles with full version history. Local-first, no accounts, no cloud.

---

## 1. Target architecture (per spec)

| Component | Lang | Port | Role |
|---|---|---|---|
| `isoforge` CLI | Python + Typer + rich | — | User entrypoint; subprocess-manages the rest |
| `iso_gateway` | Go + gin + gorilla/websocket | 4747 | Sole entrypoint; REST + WS hub; serves built React bundle |
| `agent_orchestrator` | Python + FastAPI | 5001 | LLM function-calling, IsoDSL validation, conversation state |
| `render_engine` | Python + FastAPI | 5001 | Deterministic SVG/PNG/icon-bundle export (same app as agent) |
| `scene_store` | Go + chi + SQLite | 5003 | Versions, history, diff, revert, themes (same process as gateway) |
| Web preview | React + Vite + TS + shadcn/tailwind | (via 4747) | Three-pane workbench, live SVG preview |
| `@isoforge/client` | TypeScript SDK | — | Typed `GatewayClient` used by the web UI |

54 API routes, 9 tables, 3 accepted ADRs. Contracts are already pinned by the spec —
we implement to them rather than redesigning.

### Critical architectural constraint (ADR-001)
Raw LLM text is **never** parsed as design data. Only `set_scene` / `patch_scene`
tool-call payloads are trusted, and only after JSON Schema validation. Every accepted
mutation produces a new immutable `scene_versions` row. This must be enforced in code,
not prompts.

---

## 2. Proposed repo layout

```
isoforge/
├── schemas/isodsl/v1/isodsl.schema.json   # single source of truth, vendored to all services
├── cmd/
│   ├── iso-gateway/                       # Go main
│   └── scene-store/                       # Go main
├── internal/                              # shared Go: isodsl validate, httpx, config
├── services/
│   ├── agent_orchestrator/                # Python pkg
│   └── render_engine/                     # Python pkg
├── cli/isoforge/                          # Typer CLI (the pipx-installed package)
├── web/                                   # Vite React app
├── packages/client/                       # @isoforge/client TS SDK (generated types)
├── testdata/scenes/                       # golden IsoDSL fixtures
└── Makefile / Taskfile
```

Go modules: one `go.mod` at root with two `cmd/` binaries (shared `internal/` for the
IsoDSL validator + client structs). Python: one `uv` workspace with two packages plus
the CLI, sharing an `isodsl` model package (pydantic models generated from the schema).

---

## 3. The IsoDSL schema — do this first

Nothing else can be built or tested until this exists. The spec describes it but never
defines it, so this is the main design work we own.

Sketch to ratify before coding:

```jsonc
{
  "isodsl_version": "1.0.0",
  "canvas": { "width": 512, "height": 512, "background": null },
  "grid":   { "w": 8, "d": 8, "h": 8, "cell": 32 },      // 1x1x1 unit cells
  "camera": { "projection": "isometric", "angle": 30 },
  "palette": { "id": "…", "colors": { "top": "#…", "left": "#…", "right": "#…", "accent": "#…" } },
  "shapes": [
    { "id": "c1", "type": "cube", "at": {"x":0,"y":0,"z":0},
      "size": {"x":1,"y":1,"z":1},
      "faces": { "top": {"fill":"@palette.top"}, "left": {...}, "right": {...} },
      "opacity": 1, "visible": true }
    // type ∈ cube | slab | ramp | cylinder | plane | group
  ],
  "effects": { "outline": {...}, "shadow": {...}, "glow": {...} }
}
```

Hard rules baked into the schema:
- integer grid coordinates only, bounded by `grid`
- `shapes[].id` unique; deterministic paint order = **depth sort by `(x+y+z)`**, ties broken by `id` — never array order, so output is stable across patches
- colors are `#RRGGBB(AA)` or `@palette.*` references, nothing else
- `additionalProperties: false` everywhere (this is what makes LLM output trustworthy)

Deliverables: `isodsl.schema.json`, a Go validator (`santhosh-tekuri/jsonschema`), a
Python validator (`jsonschema` + generated pydantic models), and 8–10 golden fixtures
in `testdata/scenes/` used by every layer's tests.

---

## 4. Milestones

### M0 — Foundations (½ day)
- Repo scaffold, `go.mod`, `uv` workspace, Makefile (`make dev`, `make test`, `make build`)
- `schemas/isodsl/v1` + Go & Python validators + golden fixtures
- CI: go test, pytest, tsc, schema-validate-fixtures

**Exit:** `make test` green on an empty system; fixtures validate identically in Go and Python.

### M1 — Deterministic render engine (1–2 days) ← *build this before the LLM*
Pure function `scene JSON → SVG bytes`. No service, no LLM. Just isometric projection math.
- `iso_project(x,y,z) → (sx,sy)`, face polygon generation, depth sort, face shading
- `svgwrite` output with **byte-stable** serialization (sorted attrs, fixed float precision, no timestamps/uuids)
- Golden-file tests: `sha256(render(fixture))` pinned in CI — this is the product's core promise
- PNG via `resvg-py` (preferred: no Cairo system dep) with `cairosvg` fallback; icon bundles (16→512, favicon.ico, .icns-ready) with padding/safe-zone
- Wrap in FastAPI: `/render/svg`, `/render/png`, `/export/*`, `/import/isoforge-json`, `/diff/render`, `/exports/{id}[/download]`

**Exit:** `curl` a fixture → identical SVG bytes on macOS and Linux; icon bundle zip produced.

### M2 — scene_store (1 day)
- SQLite schema + migrations for all 9 tables (spec uses `UUID`/`TIMESTAMPTZ`/`JSONB`; map to `TEXT`/`TEXT ISO-8601`/`TEXT` + `json_valid()` CHECK, UUIDv4 generated in Go)
- Canonical scene/theme JSON written to `SCENES_DIR` as files; SQLite holds metadata + history (ADR-003)
- Immutable append-only `scene_versions`; `projects.current_scene_version_id` is the head pointer
- `revert` = **new version** whose `scene_json` copies an older one (never destructive)
- Diff via RFC-6902 JSON Patch between two versions
- Schema validation enforced on every write
- All 23 `scene_store` routes + builtin themes seeded on first boot

**Exit:** create project → 5 versions → history/diff/revert round-trips; kill & restart, state intact.

### M3 — agent_orchestrator (2 days)
- Provider interface with three adapters: OpenAI, Anthropic, Ollama (Ollama uses constrained JSON output as the function-calling shim)
- Two tools exposed to the LLM: `set_scene(scene)` and `patch_scene(ops[])` (RFC-6902)
- **Validate → repair loop:** invalid payload → feed schema errors back, retry (max 2), then fail loudly. Never persist unvalidated output.
- Theme lock: when locked, reject/clamp any color outside the active palette before validation
- Conversation + messages persistence, `/reload` for round-trip editing from a `.isoforge.json`
- Good system prompt + few-shot fixtures; `/tools` introspection endpoint

**Exit:** `"a purple cube with a glowing top"` → valid scene; `"make it teal"` → minimal patch, not a full rewrite.

### M4 — iso_gateway (1 day)
- gin server on 4747; reverse-proxy/coordinate the three backends
- `POST /sessions/{id}/messages` orchestrates: agent turn → validate → store version → broadcast over WS → return to CLI
- `GET /ws/preview/{session_id}`: gorilla/websocket hub, fan-out `scene.updated` / `export.complete` / `validation.failed`, with heartbeat + reconnect/backoff
- Embed the built React bundle via `go:embed`, SPA fallback on `GET /*`
- Resilience (ADR-002): backend process crash → structured error to CLI/UI, gateway stays up

**Exit:** two browser tabs + CLI all see the same scene update within ~100ms.

### M5 — CLI (1 day) ← *first fully usable product*
Typer + rich. Subprocess supervisor: pick free ports, start all 3 backends, health-gate, open browser, tear down cleanly on exit/SIGINT.

```
isoforge chat [--model …] [--continue scene.isoforge.json] [--no-browser]
isoforge history | diff <a> <b> | revert <v>
isoforge export svg|png|icons [--size …] [--out …]
isoforge theme save|list|import|apply
```
In-chat slash commands: `/theme`, `/revert`, `/export`, `/undo`, `/json`.
Rich TUI: streaming reply, inline colored DSL diff, ASCII-art fallback preview.

**Exit:** `pipx install .` → `isoforge chat` → describe → export PNG, all in one terminal.

### M6 — Web workbench (2–3 days)
- Vite + React + TS + tailwind + shadcn/ui, dark-mode-first, accent color driven by active palette
- `@isoforge/client` SDK first (types generated from the route table), then UI consumes only that
- Routes: `/`, `/sessions/:id` (three-pane workbench), `/sessions/:id/history`, `/themes`, `/sessions/:id/export`
- Components: `IsoPreviewCanvas` (client-side SVG projection — **must share the exact same projection math as M2**, port it to TS with the same golden fixtures), `DSLDiffViewer`, `ValidationBadge`, `CommandPalette` (⌘K), `AppShellSidebar`, `ConnectionStatusIndicator`, `ToastStack`, `LoadingSkeletonScene`, `ConfirmDangerDialog`, `SlashCommandHint`
- zustand for session/scene state, react-query for server state, WS pushes invalidate

**Exit:** chat in the browser, watch the cube rebuild live, scrub the version filmstrip, export.

### M7 — Packaging & polish (1 day)
- Cross-compile Go binaries (darwin/linux × arm64/amd64), ship inside the Python wheel
- `pipx install isoforge` works from a clean machine; PyInstaller path as alternative
- Docs, example scenes, README with a rendered logo made *by* IsoForge (dogfood)

---

## 5. Sequencing rationale

```
M0 schema ──┬── M1 render ──┐
            ├── M2 store ───┼── M4 gateway ── M5 CLI ── M7 package
            └── M3 agent ───┘                     └──── M6 web
```
M1/M2/M3 are independent after M0 and can be parallelized. **M5 is the first shippable
increment** — the CLI + backends is a complete product without the web UI. M6 is the
biggest surface but lowest risk since it only consumes the SDK.

Rough total: ~10–12 focused days solo.

---

## 6. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| **Determinism drift** — same JSON → different bytes across OS/lib versions | Pin SVG serialization ourselves (don't trust lib ordering); sha256 golden tests in CI on macOS + Linux; fixed float precision |
| **Two renderers diverge** (Python engine vs TS preview) | Shared fixture suite asserting identical projection coords; TS port is a direct transliteration, reviewed as such |
| **LLM emits invalid IsoDSL** | `additionalProperties:false` + validate→repair loop (max 2) + never persist unvalidated; log rejection rate as a metric |
| **LLM rewrites whole scene instead of patching** | Prompt + tool design bias toward `patch_scene`; measure patch-vs-full ratio in tests |
| **Cairo system dependency breaks pipx installs** | Default to `resvg-py` (static, no system libs); `cairosvg` only as opt-in fallback |
| **4 processes to supervise** | Health-gated startup with timeout, structured shutdown, port auto-selection, `isoforge doctor` command |
| **Spec's 54 routes ≫ MVP need** | Implement the ~20 on the critical path first; stub the rest behind a route table so shape is right |
| **SQLite/Postgres type mismatch in spec** | Explicit mapping documented in migration file; don't silently reinterpret |

### Scope decisions — RATIFIED
1. **Two processes, not four.** Module boundaries and HTTP contracts from the spec are
   preserved, but collapsed into two runtimes:
   - `isoforged` (Go): gateway on **:4747** + scene store on **:5003**, one process. The
     gateway calls the store through an in-process Go interface (no HTTP hop); the :5003
     listener still exists for debugging and contract tests.
   - `isoforge-py` (Python): agent orchestrator + render engine in one FastAPI app on
     **:5001**. Route namespaces don't collide (only `/health`, which is merged). The
     gateway points both `AGENT_SERVICE_URL` and `RENDER_SERVICE_URL` at it.
   Either half can be split back out later without touching a single route.
2. **No auth.** Localhost-only, single user. `auth_required` in the spec is ignored;
   the `api_sessions` table is kept for session bookkeeping only.
3. **Providers:** OpenRouter (default), Anthropic, Ollama. No direct OpenAI adapter —
   OpenRouter covers it and is the dev default.

---

## 7. Immediate next actions

1. Ratify the IsoDSL schema sketch in §3 (shape primitives + effects list is the main open question)
2. Answer the three scope questions in §6
3. Then: M0 scaffold + schema + fixtures, immediately followed by M1's golden-file render tests
