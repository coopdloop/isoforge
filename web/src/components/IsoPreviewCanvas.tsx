/**
 * Deterministic SVG renderer for the browser.
 *
 * Uses the same geometry pipeline as the Python exporter (see src/iso/), so what the
 * user sees here is exactly what lands in the exported file.
 */

import { useMemo } from 'react'

import type { Scene } from '@/iso/geometry'
import { effectDefs, pointsAttr, sceneFaces, viewport } from '@/iso/render'
import { resolveColor } from '@/iso/color'

interface Props {
  scene: Scene | null | undefined
  className?: string
  /** Highlight one shape, used when hovering a diff entry. */
  highlightShapeId?: string | null
  /** Draw a faint ground grid regardless of the scene's own setting. */
  showGrid?: boolean
  onShapeClick?: (shapeId: string) => void
}

export function IsoPreviewCanvas({
  scene,
  className,
  highlightShapeId,
  showGrid,
  onShapeClick,
}: Props) {
  const model = useMemo(() => {
    if (!scene) return null
    const faces = sceneFaces(scene)
    const view = viewport(scene, faces)
    return { faces, view, effects: effectDefs(scene, view.scale) }
  }, [scene])

  if (!scene || !model) {
    return (
      <div className={`flex items-center justify-center text-zinc-600 ${className ?? ''}`}>
        <span className="text-sm">no design yet</span>
      </div>
    )
  }

  const { faces, view, effects } = model
  const palette = scene.palette?.colors ?? {}
  const background = scene.canvas?.background
    ? resolveColor(scene.canvas.background, palette)
    : null

  return (
    <svg
      viewBox={`0 0 ${view.width} ${view.height}`}
      className={className}
      role="img"
      aria-label={scene.meta?.name ?? 'isometric logo preview'}
      preserveAspectRatio="xMidYMid meet"
    >
      {(effects.shadow || effects.glow) && (
        <defs>
          <filter id="iso-effects" x="-75%" y="-75%" width="250%" height="250%">
            {effects.glow && (
              <feDropShadow
                dx="0"
                dy="0"
                stdDeviation={effects.glow.radius}
                floodColor={effects.glow.color}
                floodOpacity={effects.glow.intensity}
              />
            )}
            {effects.shadow && (
              <feDropShadow
                dx={effects.shadow.dx}
                dy={effects.shadow.dy}
                stdDeviation={effects.shadow.blur}
                floodColor={effects.shadow.color}
                floodOpacity={effects.shadow.opacity}
              />
            )}
          </filter>
        </defs>
      )}

      {background && (
        <rect x={0} y={0} width={view.width} height={view.height} fill={background.slice(0, 7)} />
      )}

      {showGrid && <GroundGrid scene={scene} view={view} />}

      <g
        transform={`translate(${view.tx},${view.ty}) scale(${view.scale})`}
        filter={effects.filterId ? `url(#${effects.filterId})` : undefined}
      >
        {faces.map((face, i) => {
          const dimmed = highlightShapeId != null && face.shapeId !== highlightShapeId
          let stroke = face.stroke
          let strokeWidth = face.strokeWidth
          if (!stroke && effects.outline?.faceScope) {
            stroke = effects.outline.color
            strokeWidth = effects.outline.width
          }
          if (highlightShapeId === face.shapeId) {
            stroke = '#FFFFFF'
            strokeWidth = 1.5 / view.scale
          }

          return (
            <polygon
              key={`${face.shapeId}-${face.face}-${i}`}
              points={pointsAttr(face.points)}
              fill={face.fill}
              fillOpacity={dimmed ? face.opacity * 0.25 : face.opacity}
              stroke={stroke}
              strokeWidth={stroke && strokeWidth > 0 ? strokeWidth : undefined}
              strokeLinejoin="round"
              onClick={onShapeClick ? () => onShapeClick(face.shapeId) : undefined}
              className={onShapeClick ? 'cursor-pointer' : undefined}
            />
          )
        })}
      </g>
    </svg>
  )
}

/** Faint ground grid to make the unit cells legible while editing. */
function GroundGrid({
  scene,
  view,
}: {
  scene: Scene
  view: { scale: number; tx: number; ty: number }
}) {
  const cell = scene.grid?.cell ?? 32
  const { w, d } = scene.grid ?? { w: 4, d: 4 }

  const lines: Array<{ x1: number; y1: number; x2: number; y2: number }> = []
  const proj = scene.camera?.projection ?? 'isometric'
  const COS30 = Math.sqrt(3) / 2

  const to = (x: number, y: number): [number, number] =>
    proj === 'dimetric'
      ? [(x - y) * cell, (x + y) * 0.5 * cell]
      : [(x - y) * COS30 * cell, (x + y) * 0.5 * cell]

  for (let i = 0; i <= w; i++) {
    const [x1, y1] = to(i, 0)
    const [x2, y2] = to(i, d)
    lines.push({ x1, y1, x2, y2 })
  }
  for (let j = 0; j <= d; j++) {
    const [x1, y1] = to(0, j)
    const [x2, y2] = to(w, j)
    lines.push({ x1, y1, x2, y2 })
  }

  return (
    <g transform={`translate(${view.tx},${view.ty}) scale(${view.scale})`} opacity={0.12}>
      {lines.map((l, i) => (
        <line
          key={i}
          x1={l.x1}
          y1={l.y1}
          x2={l.x2}
          y2={l.y2}
          stroke="currentColor"
          strokeWidth={1 / view.scale}
        />
      ))}
    </g>
  )
}
