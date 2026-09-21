/**
 * Scene tessellation for the browser preview.
 *
 * Produces the same polygons, in the same order, with the same colors as the Python
 * exporter. The preview draws these as React SVG elements rather than serializing text,
 * but the geometry and styling pipeline is identical.
 */

import { num } from './canonical'
import { resolveColor, shade, withOpacity } from './color'
import {
  bounds,
  FACE_PAINT_ORDER,
  flatten,
  paintOrder,
  TESSELLATORS,
  type Face,
  type FlatShape,
  type PlacedFace,
  type Scene,
} from './geometry'

function faceStyle(
  shape: FlatShape,
  face: Face,
  palette: Record<string, string>,
  shading: NonNullable<Scene['shading']>,
): { fill: string; opacity: number; stroke?: string; strokeWidth: number } {
  const override = shape.faces[face] ?? {}
  const base = resolveColor(shape.fill, palette)

  let fill: string
  if (override.fill !== undefined) {
    fill = resolveColor(override.fill, palette)
  } else if (shading.enabled !== false) {
    fill = shade(base, shading[face] ?? 0)
  } else {
    fill = base
  }

  const [hex, alpha] = withOpacity(fill, override.opacity ?? 1)
  return {
    fill: hex,
    opacity: num(alpha * shape.inheritedOpacity),
    stroke: override.stroke !== undefined ? resolveColor(override.stroke, palette) : undefined,
    strokeWidth: override.strokeWidth ?? 0,
  }
}

/** Produce every polygon of a scene, in exact paint order. */
export function sceneFaces(scene: Scene): PlacedFace[] {
  const grid = scene.grid ?? { w: 1, d: 1, h: 1 }
  const camera = scene.camera ?? {}
  const palette = scene.palette?.colors ?? {}
  const shading = scene.shading ?? {}
  const cell = grid.cell ?? 32
  const proj = camera.projection ?? 'isometric'

  const out: PlacedFace[] = []
  for (const shape of paintOrder(flatten(scene))) {
    const tess = TESSELLATORS[shape.type]
    if (!tess) continue

    const geometry = tess(shape, cell, proj)
    for (const [faceName, points] of Object.entries(geometry)) {
      if (!points || points.length < 3) continue
      const face = faceName as Face
      const style = faceStyle(shape, face, palette, shading)

      let stroke = style.stroke
      let strokeWidth = style.strokeWidth
      if (shape.type === 'plane' && shape.raw.stroke) {
        stroke = resolveColor(shape.raw.stroke, palette)
        strokeWidth = shape.raw.strokeWidth ?? 0
      }

      out.push({
        shapeId: shape.id,
        face,
        points,
        fill: style.fill,
        opacity: style.opacity,
        stroke,
        strokeWidth,
        order: FACE_PAINT_ORDER[face],
      })
    }
  }
  return out
}

export interface Viewport {
  scale: number
  tx: number
  ty: number
  width: number
  height: number
}

/**
 * Compute the fitted transform.
 *
 * Auto-fit keeps framing stable as shapes come and go, which matters because the user
 * is iterating conversationally and should not see the logo jump around between turns.
 */
export function viewport(scene: Scene, faces: PlacedFace[]): Viewport {
  const canvas = scene.canvas ?? { width: 512, height: 512 }
  const width = canvas.width ?? 512
  const height = canvas.height ?? 512
  const padding = canvas.padding ?? 0.08
  const camera = scene.camera ?? {}

  if (faces.length === 0 || camera.fit === false) {
    return { scale: 1, tx: num(width / 2), ty: num(height / 2), width, height }
  }

  const [minX, minY, maxX, maxY] = bounds(faces)
  const spanX = Math.max(maxX - minX, 1e-9)
  const spanY = Math.max(maxY - minY, 1e-9)
  const availW = width * (1 - 2 * padding)
  const availH = height * (1 - 2 * padding)
  const scale = Math.min(availW / spanX, availH / spanY)

  return {
    scale: num(scale),
    tx: num(width / 2 - ((minX + maxX) / 2) * scale),
    ty: num(height / 2 - ((minY + maxY) / 2) * scale),
    width,
    height,
  }
}

export interface EffectDefs {
  filterId?: string
  shadow?: { color: string; opacity: number; blur: number; dx: number; dy: number }
  glow?: { color: string; radius: number; intensity: number }
  outline?: { color: string; width: number; faceScope: boolean }
}

/** Resolve effect definitions, matching the exporter's chained-filter behaviour. */
export function effectDefs(scene: Scene, scale: number): EffectDefs {
  const palette = scene.palette?.colors ?? {}
  const effects = scene.effects ?? {}
  const out: EffectDefs = {}

  const shadow = effects.shadow
  const glow = effects.glow
  if (shadow?.enabled) {
    const offset = shadow.offset ?? {}
    out.shadow = {
      color: resolveColor(shadow.color ?? '#000000', palette),
      opacity: shadow.opacity ?? 0.25,
      blur: (shadow.blur ?? 4) / 2,
      dx: offset.x ?? 0,
      dy: offset.y ?? 4,
    }
  }
  if (glow?.enabled) {
    out.glow = {
      color: resolveColor(glow.color ?? '#FFFFFF', palette),
      radius: (glow.radius ?? 8) / 2,
      intensity: glow.intensity ?? 0.6,
    }
  }
  if (out.shadow || out.glow) out.filterId = 'iso-effects'

  const outline = effects.outline
  if (outline?.enabled) {
    out.outline = {
      color: resolveColor(outline.color ?? '#000000', palette),
      // Stroke width is specified in canvas pixels; divide out the fit scale so line
      // weight stays visually constant however far the scene was zoomed.
      width: num((outline.width ?? 1) / scale),
      faceScope: (outline.scope ?? 'face') === 'face',
    }
  }

  return out
}

export function pointsAttr(points: readonly (readonly [number, number])[]): string {
  return points.map(([x, y]) => `${x},${y}`).join(' ')
}
