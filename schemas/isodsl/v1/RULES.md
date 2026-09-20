# IsoDSL v1 — semantic rules

JSON Schema validates *shape*. These rules validate *meaning*, and are enforced
identically by the Go validator (`internal/isodsl`) and the Python validator
(`services/isoforge_py/isodsl`). A document is valid only if it passes both layers.

Every rule has a stable code so the agent repair loop can feed precise errors back to
the LLM rather than a generic "invalid".

## Referential integrity

| Code | Rule |
|---|---|
| `ISO001` | Every `id` is unique across the entire flattened shape tree, groups included. |
| `ISO002` | Every `@palette.<name>` reference resolves to a key in `palette.colors`. |
| `ISO003` | When `palette.locked` is true, no literal `#RRGGBB` color may appear anywhere in `shapes` or `effects`. |

## Geometry

| Code | Rule |
|---|---|
| `ISO010` | Every shape's absolute origin (own `at` plus all ancestor group `at` offsets) lies within `[0,grid.w) x [0,grid.d) x [0,grid.h)`. |
| `ISO011` | A shape's absolute extent (`origin + size`) must not exceed the grid bounds. |
| `ISO012` | Group nesting depth must not exceed 8. |
| `ISO013` | A `plane` must have a size of 0 on its normal axis, or omit that component entirely. |

## Determinism

| Code | Rule |
|---|---|
| `ISO020` | Paint order is `(x + y + z)` ascending on the absolute origin, ties broken by ascending `id` (byte order). Array order is never consulted. |
| `ISO021` | All floats serialize with exactly 4 decimal places, half-up rounding, no exponent, no negative zero. |
| `ISO022` | Object keys serialize in the order declared by the schema, not insertion or alphabetical order. |

### Why `(x + y + z)`

In the isometric projection used here, screen depth increases monotonically with
`x + y + z`. Two shapes with equal sums never visually overlap, so breaking the tie on
`id` is safe and — crucially — stable. This means adding a shape via `patch_scene`
cannot silently restack unrelated geometry, which is what makes diffs trustworthy.

## Projection reference

The canonical transform, shared byte-for-byte between the Python engine and the
TypeScript preview renderer:

```
isometric:  sx = (x - y) * cos(30°) * cell
            sy = (x + y) * sin(30°) * cell - z * cell

dimetric:   sx = (x - y) * cell
            sy = (x + y) * cell * 0.5 - z * cell
```

`cos(30°)` and `sin(30°)` are evaluated as `sqrt(3)/2` and `0.5` exactly, then the
result is rounded per `ISO021`. Both implementations are pinned to the same fixture
coordinate table in `testdata/projection.json`.

## Auto-shading

When a face has no explicit `fill`, the renderer derives it from `shape.fill` by
applying the `shading` offset for that face in **OKLab** lightness space, then
converting back to sRGB. OKLab keeps perceptual spacing even across saturated brand
colors, where naive HSL shifts go muddy. Offsets are clamped to `[0,1]` lightness
before conversion, and the conversion is pinned by fixture so both renderers agree.
