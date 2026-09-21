# IsoForge — implementation notes

A CLI that turns a conversation into an isometric cube-art logo. One Python process,
no server, no database.

## Layout

```
isoforge/
├── isodsl/      schema, validation, geometry, color     ~1,400 lines
├── agent/       LLM tool-calling, repair loop, prompts  ~1,300
├── render/      SVG, PNG, icon bundles                    ~530
├── cli.py       chat REPL and commands                    ~750
├── store.py     projects and versions as JSON files        ~260
└── diff.py      identity-based scene diffing               ~110
```

Plus `schemas/isodsl/v1/` (the shared schema and its semantic rules) and `testdata/`
(fixtures and golden SVG bytes).

## Design decisions

**IsoDSL is the product.** The spec never defined it, so it was the real design work.
`additionalProperties: false` everywhere is what makes LLM output trustworthy: the
model cannot invent a field, and every rejection carries a stable error code
(`ISO001`…) with remedy text that feeds the repair loop.

**Paint order is `(x+y+z)` then `id`, never array order.** This is what lets a patch
add a shape without silently restacking unrelated geometry — the property that makes
diffs trustworthy.

**OKLab for auto-shading.** One `fill` derives three faces. Naive HSL lightness shifts
turn saturated brand colors muddy; OKLab keeps them on-hue.

**Versions are files, not rows.** `v1.isoforge.json`, `v2.isoforge.json`, … in a
project directory. History is a directory listing, diff is reading two files, revert is
copying one forward. More git-friendly than a database, which matters for a tool whose
premise is designs you can version like source.

**Every `chat` starts a new design.** Continuing is explicit (`--resume` or `-P`).
The alternative — reusing a shared "untitled" project — silently appended a second
design's versions onto the first, which is the opposite of what starting a new chat
means. Unnamed designs get a timestamped handle; a name collision with an existing
design that has work in it is suffixed rather than merged.

**Revert appends, never truncates.** Reverting to v2 from v5 creates v6. In a tool
where reverts are cheap and frequent, destroying v3–v5 would be a nasty surprise.

**Diffs match shapes by `id`, not position.** A positional diff reports "everything
changed" when one shape is inserted at the front — useless in a UI whose job is showing
what the model actually edited.

**Determinism is enforced by test.** Golden SVG bytes are pinned and verified to catch
drift as small as `1e-4` in a projection constant. Fixed float precision, schema key
order, and stable filter ids; no timestamps or random ids in output.

## History

This started as a generated product spec describing four microservices (Go gateway, Go
store, two Python services), a SQLite database with nine tables, a React workbench, and
a TypeScript SDK. That version was built and works — it is preserved on the
`full-architecture` branch.

It was then stripped back, because the actual ask was a CLI with a feedback loop that
ends in a logo. The measurements that prompted it:

| | lines |
|---|---|
| Product logic (DSL, renderer, agent, CLI) | ~4,070 |
| Scaffolding for the service architecture | ~8,645 |

Two-thirds of the code existed to serve an architecture rather than the feature. What
went: a second IsoDSL validator in Go (1,190 lines) plus the cross-language hash
fixture needed to stop the two drifting, a nine-table SQLite store (1,679), an HTTP
gateway between two local processes (2,296), a process supervisor (461), a React app
(2,479), and a FastAPI layer (432).

Result: 3 languages → 1, 2 processes → 1, a 9.8 MB platform-specific wheel → a 59 KB
universal one, and no pnpm or Go toolchain to build.

What was lost: the live browser preview and multi-client WebSocket sessions. The
terminal preview covers the feedback loop, and `isoforge export png` covers the rest.

## Possible next steps

- Prompt tuning: the model sometimes overlaps shapes that should be spaced apart,
  and the first version often needs 2-3 corrections before the silhouette reads well
- `isoforge watch` to re-export on file change, for embedding in a README
- Publish to PyPI (the release workflow is wired, but needs a trusted-publisher setup)
