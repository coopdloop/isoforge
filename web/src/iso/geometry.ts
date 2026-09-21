/**
 * Isometric projection and deterministic paint ordering.
 *
 * This is a direct transliteration of services/isoforge_py/isodsl/geometry.py, not an
 * independent implementation. It is pinned to the same coordinate fixtures, because if
 * the browser preview and the exported file ever disagree the product's core promise
 * breaks. When changing anything here, change the Python side in the same commit.
 */

import { num } from './canonical'

/** Exact trig for the isometric transform: sqrt(3)/2 and 1/2. */
export const COS30 = Math.sqrt(3.0) / 2.0
export const SIN30 = 0.5

export type Face = 'top' | 'left' | 'right'
export type Point = readonly [number, number]

export interface GridCoord {
  x: number
  y: number
  z: number
}

export interface SceneShape {
  id: string
  type: 'cube' | 'ramp' | 'cylinder' | 'plane' | 'group'
  at: GridCoord
  size?: Partial<GridCoord>
  fill?: string
  faces?: Partial<Record<Face, FaceStyle>>
  opacity?: number
  visible?: boolean
  label?: string
  bevel?: number
  facing?: 'north' | 'east' | 'south' | 'west'
  orientation?: Face
  segments?: number
  stroke?: string
  strokeWidth?: number
  children?: SceneShape[]
}

export interface FaceStyle {
  fill?: string
  opacity?: number
  stroke?: string
  strokeWidth?: number
}

export interface Scene {
  isodsl_version: string
  meta?: { name?: string; description?: string; tags?: string[] }
  canvas: { width: number; height: number; background?: string | null; padding?: number }
  grid: { w: number; d: number; h: number; cell?: number; show?: boolean }
  camera?: { projection?: 'isometric' | 'dimetric'; fit?: boolean }
  palette: { id?: string; name?: string; locked?: boolean; colors: Record<string, string> }
  shading?: { enabled?: boolean; top?: number; left?: number; right?: number }
  effects?: {
    outline?: { enabled?: boolean; color?: string; width?: number; scope?: 'face' | 'silhouette' }
    shadow?: {
      enabled?: boolean
      color?: string
      opacity?: number
      blur?: number
      offset?: { x?: number; y?: number }
    }
    glow?: { enabled?: boolean; color?: string; radius?: number; intensity?: number }
  }
  shapes: SceneShape[]
}

/**
 * Map a grid coordinate to screen space.
 *
 * +x goes right-and-down, +y goes left-and-down, +z goes up. Screen depth increases
 * monotonically with (x + y + z), which is what makes the paint-order rule sound.
 */
export function project(
  x: number,
  y: number,
  z: number,
  cell = 1.0,
  projection: string = 'isometric',
): Point {
  if (projection === 'dimetric') {
    return [num((x - y) * cell), num(((x + y) * 0.5 - z) * cell)]
  }
  return [num((x - y) * COS30 * cell), num(((x + y) * SIN30 - z) * cell)]
}

export interface FlatShape {
  id: string
  type: string
  origin: readonly [number, number, number]
  size: readonly [number, number, number]
  fill?: string
  faces: Partial<Record<Face, FaceStyle>>
  opacity: number
  raw: SceneShape
  inheritedOpacity: number
}

export interface PlacedFace {
  shapeId: string
  face: Face
  points: Point[]
  fill: string
  opacity: number
  stroke?: string
  strokeWidth: number
  order: number
}

const DEFAULT_SIZE: readonly [number, number, number] = [1, 1, 1]
const FACE_PAINT_ORDER: Record<Face, number> = { left: 0, right: 1, top: 2 }

/** Resolve groups into a flat list of absolutely-positioned shapes. */
export function flatten(scene: Scene): FlatShape[] {
  const out: FlatShape[] = []

  const walk = (
    shapes: SceneShape[],
    origin: readonly [number, number, number],
    opacity: number,
  ): void => {
    for (const shape of shapes) {
      if (!shape || shape.visible === false) continue

      const at = shape.at ?? { x: 0, y: 0, z: 0 }
      const absOrigin = [
        origin[0] + Math.trunc(at.x ?? 0),
        origin[1] + Math.trunc(at.y ?? 0),
        origin[2] + Math.trunc(at.z ?? 0),
      ] as const

      const effOpacity = opacity * (shape.opacity ?? 1.0)

      if (shape.type === 'group') {
        walk(shape.children ?? [], absOrigin, effOpacity)
        continue
      }

      const size = shape.size ?? {}
      out.push({
        id: shape.id ?? '',
        type: shape.type ?? '',
        origin: absOrigin,
        size: [
          size.x ?? DEFAULT_SIZE[0],
          size.y ?? DEFAULT_SIZE[1],
          size.z ?? DEFAULT_SIZE[2],
        ] as const,
        fill: shape.fill,
        faces: shape.faces ?? {},
        opacity: shape.opacity ?? 1.0,
        raw: shape,
        inheritedOpacity: effOpacity,
      })
    }
  }

  walk(scene.shapes ?? [], [0, 0, 0], 1.0)
  return out
}

export function depthOf(shape: FlatShape): number {
  return shape.origin[0] + shape.origin[1] + shape.origin[2]
}

/**
 * ISO020: sort by (x+y+z) then id. Input array order is never consulted, so a patch
 * cannot silently restack unrelated geometry.
 */
export function paintOrder(shapes: FlatShape[]): FlatShape[] {
  return [...shapes].sort((a, b) => {
    const da = depthOf(a)
    const db = depthOf(b)
    if (da !== db) return da - db
    // Byte-order comparison to match Python's string ordering exactly.
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
  })
}

function cubeFaces(shape: FlatShape, cell: number, proj: string): Partial<Record<Face, Point[]>> {
  const [x, y, z] = shape.origin
  const [sx, sy, sz] = shape.size
  const x1 = x + sx
  const y1 = y + sy
  const z1 = z + sz
  const p = (a: number, b: number, c: number) => project(a, b, c, cell, proj)

  return {
    top: [p(x, y, z1), p(x1, y, z1), p(x1, y1, z1), p(x, y1, z1)],
    left: [p(x, y1, z1), p(x1, y1, z1), p(x1, y1, z), p(x, y1, z)],
    right: [p(x1, y, z1), p(x1, y1, z1), p(x1, y1, z), p(x1, y, z)],
  }
}

function rampFaces(shape: FlatShape, cell: number, proj: string): Partial<Record<Face, Point[]>> {
  const [x, y, z] = shape.origin
  const [sx, sy, sz] = shape.size
  const x1 = x + sx
  const y1 = y + sy
  const z1 = z + sz
  const facing = shape.raw.facing ?? 'south'
  const p = (a: number, b: number, c: number) => project(a, b, c, cell, proj)

  /** Height of the sloped top surface at a base corner: high opposite `facing`. */
  const h = (cx: number, cy: number): number => {
    if (facing === 'east') return cx === x ? z1 : z
    if (facing === 'west') return cx === x1 ? z1 : z
    if (facing === 'north') return cy === y1 ? z1 : z
    return cy === y ? z1 : z
  }

  const quad = (corners: Array<readonly [number, number]>): Point[] => {
    const topEdge = corners.map(([cx, cy]) => p(cx, cy, h(cx, cy)))
    const bottomEdge = [...corners].reverse().map(([cx, cy]) => p(cx, cy, z))
    const pts = [...topEdge, ...bottomEdge]

    // Drop consecutive duplicates so a collapsed face becomes a triangle or vanishes.
    const result: Point[] = []
    for (const pt of pts) {
      const last = result[result.length - 1]
      if (!last || last[0] !== pt[0] || last[1] !== pt[1]) result.push(pt)
    }
    const first = result[0]
    const final = result[result.length - 1]
    if (result.length > 1 && first[0] === final[0] && first[1] === final[1]) result.pop()
    return result
  }

  return {
    top: [p(x, y, h(x, y)), p(x1, y, h(x1, y)), p(x1, y1, h(x1, y1)), p(x, y1, h(x, y1))],
    left: quad([
      [x, y1],
      [x1, y1],
    ]),
    right: quad([
      [x1, y],
      [x1, y1],
    ]),
  }
}

function cylinderFaces(
  shape: FlatShape,
  cell: number,
  proj: string,
): Partial<Record<Face, Point[]>> {
  const [x, y, z] = shape.origin
  const [sx, sy, sz] = shape.size
  const cx = x + sx / 2.0
  const cy = y + sy / 2.0
  const rx = sx / 2.0
  const ry = sy / 2.0
  const z1 = z + sz
  const segs = Math.trunc(shape.raw.segments ?? 48)

  const ring: Array<readonly [number, number]> = []
  for (let i = 0; i < segs; i++) {
    const theta = (2.0 * Math.PI * i) / segs
    ring.push([cx + rx * Math.cos(theta), cy + ry * Math.sin(theta)] as const)
  }

  const top = ring.map(([a, b]) => project(a, b, z1, cell, proj))

  // Side wall: the silhouette half of the ring, extruded down.
  let leftI = 0
  let rightI = 0
  let minX = Infinity
  let maxX = -Infinity
  ring.forEach(([a, b], i) => {
    const sxp = project(a, b, z1, cell, proj)[0]
    // Ties break on the lower index, matching Python's min()/max() on (value, index).
    if (sxp < minX) {
      minX = sxp
      leftI = i
    }
    if (sxp > maxX) {
      maxX = sxp
      rightI = i
    }
  })

  const front: number[] = []
  let i = rightI
  for (;;) {
    front.push(i)
    if (i === leftI) break
    i = (i + 1) % segs
  }

  const wall = [
    ...front.map((idx) => project(ring[idx][0], ring[idx][1], z1, cell, proj)),
    ...[...front].reverse().map((idx) => project(ring[idx][0], ring[idx][1], z, cell, proj)),
  ]

  return { top, left: wall, right: [] }
}

function planeFaces(shape: FlatShape, cell: number, proj: string): Partial<Record<Face, Point[]>> {
  const [x, y, z] = shape.origin
  const [sx, sy, sz] = shape.size
  const orientation = shape.raw.orientation ?? 'top'
  const p = (a: number, b: number, c: number) => project(a, b, c, cell, proj)

  if (orientation === 'top') {
    return { top: [p(x, y, z), p(x + sx, y, z), p(x + sx, y + sy, z), p(x, y + sy, z)] }
  }
  if (orientation === 'left') {
    return { left: [p(x, y, z + sz), p(x + sx, y, z + sz), p(x + sx, y, z), p(x, y, z)] }
  }
  return { right: [p(x, y, z + sz), p(x, y + sy, z + sz), p(x, y + sy, z), p(x, y, z)] }
}

const TESSELLATORS: Record<
  string,
  (shape: FlatShape, cell: number, proj: string) => Partial<Record<Face, Point[]>>
> = {
  cube: cubeFaces,
  ramp: rampFaces,
  cylinder: cylinderFaces,
  plane: planeFaces,
}

export { TESSELLATORS, FACE_PAINT_ORDER }

/** Axis-aligned screen bounds of every polygon. */
export function bounds(faces: PlacedFace[]): [number, number, number, number] {
  if (faces.length === 0) return [0, 0, 0, 0]
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const face of faces) {
    for (const [px, py] of face.points) {
      if (px < minX) minX = px
      if (px > maxX) maxX = px
      if (py < minY) minY = py
      if (py > maxY) maxY = py
    }
  }
  return [minX, minY, maxX, maxY]
}
