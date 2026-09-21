/**
 * JSON Patch diff viewer.
 *
 * Shows the RFC-6902 operations the model actually emitted rather than a textual
 * line diff: "one op changed /shapes/0/fill" is the honest description of the edit,
 * and it reads far better than a dozen shifted JSON lines.
 */

import type { PatchOp } from '@/lib/api'

const OP_STYLES: Record<string, { label: string; className: string; symbol: string }> = {
  add: { label: 'add', className: 'text-emerald-400', symbol: '+' },
  remove: { label: 'remove', className: 'text-rose-400', symbol: '−' },
  replace: { label: 'replace', className: 'text-amber-400', symbol: '~' },
  move: { label: 'move', className: 'text-sky-400', symbol: '→' },
  copy: { label: 'copy', className: 'text-sky-400', symbol: '⧉' },
  test: { label: 'test', className: 'text-zinc-400', symbol: '?' },
}

function summarizeValue(value: unknown): string {
  if (value === undefined) return ''
  if (value === null) return 'null'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  if (Array.isArray(value)) return `[${value.length} items]`
  const obj = value as Record<string, unknown>
  if (typeof obj.id === 'string') return `${obj.type ?? 'shape'} '${obj.id}'`
  const keys = Object.keys(obj)
  return `{${keys.slice(0, 3).join(', ')}${keys.length > 3 ? ', …' : ''}}`
}

/** Colour swatch for values that are obviously colours. */
function ColorChip({ value }: { value: unknown }) {
  if (typeof value !== 'string' || !/^#[0-9a-fA-F]{6}/.test(value)) return null
  return (
    <span
      className="ml-1.5 inline-block h-3 w-3 rounded-sm align-middle ring-1 ring-white/20"
      style={{ backgroundColor: value.slice(0, 7) }}
    />
  )
}

interface Props {
  ops: PatchOp[]
  className?: string
  onHoverShape?: (shapeId: string | null) => void
  emptyMessage?: string
}

export function DSLDiffViewer({ ops, className, onHoverShape, emptyMessage }: Props) {
  if (ops.length === 0) {
    return (
      <p className={`text-sm text-zinc-500 ${className ?? ''}`}>
        {emptyMessage ?? 'no changes'}
      </p>
    )
  }

  return (
    <ul className={`space-y-0.5 font-mono text-xs ${className ?? ''}`}>
      {ops.map((op, i) => {
        const style = OP_STYLES[op.op] ?? OP_STYLES.test
        const shapeId = shapeIdFromPath(op.path, op.value)
        return (
          <li
            key={`${op.op}-${op.path}-${i}`}
            className="flex items-baseline gap-2 rounded px-1.5 py-1 hover:bg-white/5"
            onMouseEnter={() => onHoverShape?.(shapeId)}
            onMouseLeave={() => onHoverShape?.(null)}
          >
            <span className={`w-3 shrink-0 ${style.className}`}>{style.symbol}</span>
            <span className="truncate text-zinc-300" title={op.path}>
              {op.path}
            </span>
            {op.value !== undefined && (
              <span className="ml-auto shrink-0 truncate text-zinc-400">
                {summarizeValue(op.value)}
                <ColorChip value={op.value} />
              </span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/** Best-effort shape id for an op, so hovering a row can highlight the shape. */
function shapeIdFromPath(_path: string, value: unknown): string | null {
  if (value && typeof value === 'object' && 'id' in (value as Record<string, unknown>)) {
    const id = (value as Record<string, unknown>).id
    if (typeof id === 'string') return id
  }
  return null
}

export function DiffSummaryBadges({
  summary,
}: {
  summary: { added: number; removed: number; replaced: number }
}) {
  return (
    <span className="flex items-center gap-2 font-mono text-xs">
      {summary.added > 0 && <span className="text-emerald-400">+{summary.added}</span>}
      {summary.removed > 0 && <span className="text-rose-400">−{summary.removed}</span>}
      {summary.replaced > 0 && <span className="text-amber-400">~{summary.replaced}</span>}
    </span>
  )
}
