/**
 * Cross-language conformance: the TypeScript preview must agree with the Python
 * exporter exactly.
 *
 * The fixture is generated from the Python implementation (`make conformance`). If
 * these fail, the browser is showing the user something different from what they will
 * export, which is the one failure mode that would undermine the whole product.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { fmt, num, roundHalfUp } from './canonical'
import { parseHex, shade, toHex } from './color'
import { flatten, paintOrder, project, type Scene } from './geometry'
import { sceneFaces } from './render'

const REPO_ROOT = join(__dirname, '..', '..', '..')
const GOLDEN = join(REPO_ROOT, 'testdata', 'golden', 'geometry-conformance.json')
const SCENES = join(REPO_ROOT, 'testdata', 'scenes', 'valid')

interface Conformance {
  projection: Array<{
    projection: string
    x: number
    y: number
    z: number
    cell: number
    sx: number
    sy: number
  }>
  shade: Array<{ color: string; offset: number; result: string }>
  scenes: Record<
    string,
    {
      paint_order: string[]
      face_count: number
      faces: Array<{
        shape_id: string
        face: string
        fill: string
        opacity: number
        points: number[][]
      }>
    }
  >
}

const golden: Conformance = JSON.parse(readFileSync(GOLDEN, 'utf8'))

function loadScene(name: string): Scene {
  return JSON.parse(readFileSync(join(SCENES, name), 'utf8'))
}

describe('projection matches Python', () => {
  for (const c of golden.projection) {
    it(`${c.projection} (${c.x},${c.y},${c.z}) cell=${c.cell}`, () => {
      const [sx, sy] = project(c.x, c.y, c.z, c.cell, c.projection)
      expect(sx).toBe(c.sx)
      expect(sy).toBe(c.sy)
    })
  }
})

describe('OKLab shading matches Python', () => {
  for (const c of golden.shade) {
    it(`shade(${c.color}, ${c.offset})`, () => {
      expect(shade(c.color, c.offset)).toBe(c.result)
    })
  }
})

describe('scene tessellation matches Python', () => {
  for (const [name, expected] of Object.entries(golden.scenes)) {
    describe(name, () => {
      const scene = loadScene(name)

      it('paint order is identical', () => {
        expect(paintOrder(flatten(scene)).map((s) => s.id)).toEqual(expected.paint_order)
      })

      it('produces the same number of faces', () => {
        expect(sceneFaces(scene)).toHaveLength(expected.face_count)
      })

      it('every polygon, color and opacity matches', () => {
        const actual = sceneFaces(scene)
        actual.forEach((face, i) => {
          const want = expected.faces[i]
          expect(`${face.shapeId}.${face.face}`).toBe(`${want.shape_id}.${want.face}`)
          expect(face.fill).toBe(want.fill)
          expect(face.opacity).toBe(want.opacity)
          expect(face.points.map((p) => [...p])).toEqual(want.points)
        })
      })
    })
  }
})

describe('number formatting', () => {
  const cases: Array<[number, string]> = [
    [0, '0.0'],
    [-0.0, '0.0'],
    [1, '1.0'],
    [0.5, '0.5'],
    [1e-9, '0.0'],
    [0.1 + 0.2, '0.3'],
    [1234.56789, '1234.5679'],
    [-27.712812921102035, '-27.7128'],
    [0.00005, '0.0001'],
  ]
  for (const [input, want] of cases) {
    it(`fmt(${input}) === ${want}`, () => expect(fmt(input)).toBe(want))
  }

  it('never uses scientific notation', () => {
    for (const v of [1e-20, 1e20, -1e-15]) {
      expect(fmt(v).toLowerCase()).not.toContain('e')
    }
  })

  it('rounds half up like Python, not half to even', () => {
    expect(roundHalfUp(0.00005)).toBe(0.0001)
    expect(roundHalfUp(-0.00005)).toBe(-0.0001)
  })

  it('collapses negative zero', () => {
    expect(num(-0)).toBe(0)
    expect(Object.is(num(-0), -0)).toBe(false)
  })
})

describe('color round trips', () => {
  it('preserves alpha', () => {
    expect(toHex(parseHex('#11223344'))).toBe('#11223344')
  })

  it('omits opaque alpha', () => {
    expect(toHex(parseHex('#112233FF'))).toBe('#112233')
  })

  it('rejects shorthand hex', () => {
    expect(() => parseHex('#abc')).toThrow()
  })
})

describe('paint order rules', () => {
  it('ignores array order', () => {
    const scene = loadScene('04-depth-sort-order.isoforge.json')
    const forward = paintOrder(flatten(scene)).map((s) => s.id)

    const reversed: Scene = { ...scene, shapes: [...scene.shapes].reverse() }
    expect(paintOrder(flatten(reversed)).map((s) => s.id)).toEqual(forward)
  })

  it('breaks depth ties on id', () => {
    const scene: Scene = {
      isodsl_version: '1.0.0',
      canvas: { width: 256, height: 256 },
      grid: { w: 4, d: 4, h: 4 },
      palette: { colors: { a: '#FFFFFF' } },
      shapes: [
        { id: 'zebra', type: 'cube', at: { x: 2, y: 0, z: 0 }, fill: '@palette.a' },
        { id: 'alpha', type: 'cube', at: { x: 0, y: 2, z: 0 }, fill: '@palette.a' },
        { id: 'mango', type: 'cube', at: { x: 1, y: 1, z: 0 }, fill: '@palette.a' },
      ],
    }
    expect(paintOrder(flatten(scene)).map((s) => s.id)).toEqual(['alpha', 'mango', 'zebra'])
  })

  it('flattens groups with accumulated offsets', () => {
    const scene = loadScene('03-all-primitives.isoforge.json')
    const ids = flatten(scene).map((s) => s.id)
    expect(ids).toContain('pip_a')

    const pip = flatten(scene).find((s) => s.id === 'pip_a')!
    // Group 'cluster' sits at (1,4,1) and pip_a at (0,0,0) within it.
    expect([...pip.origin]).toEqual([1, 4, 1])
  })

  it('omits invisible shapes', () => {
    const scene = loadScene('01-single-cube.isoforge.json')
    const hidden: Scene = {
      ...scene,
      shapes: scene.shapes.map((s) => ({ ...s, visible: false })),
    }
    expect(flatten(hidden)).toHaveLength(0)
  })
})
