/**
 * Color resolution and perceptual face shading.
 *
 * Transliterated from services/isoforge_py/isodsl/color.py. Shading happens in OKLab
 * rather than HSL so saturated brand colors keep their hue across the three cube faces
 * instead of going muddy.
 */

import { num } from './canonical'

const PALETTE_REF = /^@palette\.([a-z][a-zA-Z0-9_]{0,31})$/
const LITERAL = /^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$/

export interface RGBA {
  r: number
  g: number
  b: number
  a: number
}

export function parseHex(value: string): RGBA {
  const m = LITERAL.exec(value)
  if (!m) throw new Error(`not a literal IsoDSL color: ${value}`)
  const rgb = m[1]
  const alpha = m[2]
  return {
    r: parseInt(rgb.slice(0, 2), 16) / 255,
    g: parseInt(rgb.slice(2, 4), 16) / 255,
    b: parseInt(rgb.slice(4, 6), 16) / 255,
    a: alpha ? parseInt(alpha, 16) / 255 : 1,
  }
}

export function toHex(c: RGBA, forceAlpha = false): string {
  const ch = (v: number): string => {
    const i = Math.max(0, Math.min(255, Math.round(v * 255)))
    return i.toString(16).toUpperCase().padStart(2, '0')
  }
  let out = `#${ch(c.r)}${ch(c.g)}${ch(c.b)}`
  if (forceAlpha || c.a < 1) out += ch(c.a)
  return out
}

/** Resolve '@palette.x' to its literal, pass literals through, fall back on default. */
export function resolveColor(
  value: string | undefined | null,
  palette: Record<string, string>,
  fallback = '#888888',
): string {
  if (!value) return fallback
  const m = PALETTE_REF.exec(value)
  if (m) return palette[m[1]] ?? fallback
  return value
}

export function isPaletteRef(value: string): boolean {
  return PALETTE_REF.test(value)
}

// --- sRGB <-> OKLab ---------------------------------------------------------
// Bjorn Ottosson's OKLab. Constants reproduced exactly; do not "simplify" them.

function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

function linearToSrgb(c: number): number {
  return c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055
}

function cbrt(x: number): number {
  return x >= 0 ? Math.pow(x, 1 / 3) : -Math.pow(-x, 1 / 3)
}

export interface OKLab {
  L: number
  a: number
  b: number
}

export function rgbToOklab(c: RGBA): OKLab {
  const r = srgbToLinear(c.r)
  const g = srgbToLinear(c.g)
  const b = srgbToLinear(c.b)

  const l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
  const m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
  const s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b

  const l_ = cbrt(l)
  const m_ = cbrt(m)
  const s_ = cbrt(s)

  return {
    L: 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
    a: 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
    b: 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
  }
}

export function oklabToRgb(lab: OKLab, alpha = 1): RGBA {
  const l_ = lab.L + 0.3963377774 * lab.a + 0.2158037573 * lab.b
  const m_ = lab.L - 0.1055613458 * lab.a - 0.0638541728 * lab.b
  const s_ = lab.L - 0.0894841775 * lab.a - 1.291485548 * lab.b

  const l = l_ * l_ * l_
  const m = m_ * m_ * m_
  const s = s_ * s_ * s_

  const r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
  const g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
  const b = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s

  const clamp = (v: number) => Math.max(0, Math.min(1, v))
  return {
    r: clamp(linearToSrgb(r)),
    g: clamp(linearToSrgb(g)),
    b: clamp(linearToSrgb(b)),
    a: alpha,
  }
}

/**
 * Shift a color's perceptual lightness, preserving hue.
 *
 * An offset of 0 returns the input unchanged so that shading.enabled=false and a zero
 * offset are byte-identical.
 */
export function shade(color: string, offset: number): string {
  if (offset === 0) return toHex(parseHex(color))
  const base = parseHex(color)
  const lab = rgbToOklab(base)
  const shifted: OKLab = { L: Math.max(0, Math.min(1, lab.L + offset)), a: lab.a, b: lab.b }
  return toHex(oklabToRgb(shifted, base.a))
}

/** Split a possibly-8-digit color into (#RRGGBB, combined alpha) for SVG output. */
export function withOpacity(color: string, opacity: number): [string, number] {
  const c = parseHex(color)
  const combined = num(c.a * opacity)
  return [toHex({ ...c, a: 1 }), combined]
}
