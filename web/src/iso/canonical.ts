/**
 * Canonical number formatting (rule ISO021).
 *
 * Transliterated from services/isoforge_py/isodsl/canonical.py. The browser only needs
 * the number formatting, not the full serializer, but it must round identically or
 * projected coordinates will differ from the exported SVG in the last decimal place.
 */

export const PRECISION = 4

/**
 * Round half-up, matching Python's Decimal ROUND_HALF_UP.
 *
 * JavaScript's Math.round is half-up for positives but rounds -0.5 toward zero, so
 * negatives need explicit handling to stay in step with Python.
 */
export function roundHalfUp(value: number, places = PRECISION): number {
  if (!Number.isFinite(value)) return 0
  const shift = Math.pow(10, places)
  const scaled = value * shift
  const rounded = scaled < 0 ? -Math.floor(-scaled + 0.5) : Math.floor(scaled + 0.5)
  return rounded / shift
}

/** Quantise a number to canonical precision, collapsing -0 to 0. */
export function num(value: number): number {
  const rounded = roundHalfUp(value)
  return rounded === 0 ? 0 : rounded
}

/**
 * Format a number for output: fixed precision, no exponent, no negative zero.
 *
 * toFixed would emit "1e+21" beyond 1e21 and JS prints small magnitudes in scientific
 * notation; both would break byte-stability against the Python renderer.
 */
export function fmt(value: number): string {
  if (!Number.isFinite(value)) return '0.0'
  const rounded = num(value)
  let s = rounded.toFixed(PRECISION)
  if (s.includes('.')) {
    s = s.replace(/0+$/, '')
    if (s.endsWith('.')) s += '0'
  }
  // Collapse "-0.0" to "0.0".
  if (/^-0\.?0*$/.test(s)) s = s.replace('-', '')
  return s
}
