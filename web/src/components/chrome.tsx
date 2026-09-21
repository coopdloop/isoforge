/**
 * Small shared UI pieces: connection status, validation badge, toasts, palette strip.
 */

import { useEffect, useState } from 'react'

import type { ConnectionState } from '@/lib/usePreviewSocket'
import type { ValidationResult } from '@/lib/api'

const CONNECTION_STYLES: Record<ConnectionState, { label: string; dot: string; text: string }> = {
  connected: { label: 'live', dot: 'bg-emerald-400', text: 'text-emerald-400' },
  connecting: { label: 'connecting', dot: 'bg-amber-400 animate-pulse', text: 'text-amber-400' },
  reconnecting: {
    label: 'reconnecting',
    dot: 'bg-amber-400 animate-pulse',
    text: 'text-amber-400',
  },
  offline: { label: 'offline', dot: 'bg-rose-500', text: 'text-rose-400' },
}

export function ConnectionStatusIndicator({ state }: { state: ConnectionState }) {
  const style = CONNECTION_STYLES[state]
  return (
    <span
      className={`flex items-center gap-1.5 text-xs ${style.text}`}
      title={
        state === 'offline'
          ? 'the local gateway is not reachable; is `isoforge chat` still running?'
          : `preview socket ${style.label}`
      }
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  )
}

export function ValidationBadge({ result }: { result: ValidationResult | null }) {
  if (!result) return null

  if (result.valid) {
    return (
      <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-400">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
        valid
      </span>
    )
  }

  const first = result.errors[0]
  return (
    <span
      className="flex items-center gap-1.5 rounded-full bg-rose-500/10 px-2 py-0.5 text-xs text-rose-400"
      title={result.errors.map((e) => `${e.code}: ${e.message}`).join('\n')}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
      {result.errors.length} issue{result.errors.length === 1 ? '' : 's'}
      {first && <span className="font-mono opacity-70">{first.code}</span>}
    </span>
  )
}

export function PaletteStrip({
  colors,
  locked,
  className,
}: {
  colors: Record<string, string>
  locked?: boolean
  className?: string
}) {
  const entries = Object.entries(colors)
  if (entries.length === 0) return null

  return (
    <div className={`flex items-center gap-1 ${className ?? ''}`}>
      {entries.map(([name, value]) => (
        <span
          key={name}
          title={`${name}  ${value}`}
          className="h-4 w-4 rounded ring-1 ring-white/10"
          style={{ backgroundColor: value.slice(0, 7) }}
        />
      ))}
      {locked && (
        <span className="ml-1 text-[10px] uppercase tracking-wide text-zinc-500">locked</span>
      )}
    </div>
  )
}

// --- toasts -----------------------------------------------------------------

export interface Toast {
  id: number
  message: string
  tone: 'info' | 'success' | 'error'
}

let toastSeq = 0
const listeners = new Set<(toasts: Toast[]) => void>()
let toasts: Toast[] = []

export function pushToast(message: string, tone: Toast['tone'] = 'info') {
  const toast: Toast = { id: ++toastSeq, message, tone }
  toasts = [...toasts, toast]
  listeners.forEach((fn) => fn(toasts))
  setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== toast.id)
    listeners.forEach((fn) => fn(toasts))
  }, 4500)
}

const TONE_STYLES: Record<Toast['tone'], string> = {
  info: 'border-zinc-700 bg-zinc-900 text-zinc-200',
  success: 'border-emerald-700/50 bg-emerald-950/80 text-emerald-200',
  error: 'border-rose-700/50 bg-rose-950/80 text-rose-200',
}

export function ToastStack() {
  const [items, setItems] = useState<Toast[]>(toasts)

  useEffect(() => {
    listeners.add(setItems)
    return () => {
      listeners.delete(setItems)
    }
  }, [])

  if (items.length === 0) return null

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {items.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-lg ${TONE_STYLES[toast.tone]}`}
        >
          {toast.message}
        </div>
      ))}
    </div>
  )
}

export function LoadingSkeletonScene() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="relative h-32 w-32 animate-pulse">
        <div className="absolute inset-x-0 top-0 mx-auto h-16 w-24 rotate-45 skew-x-12 rounded bg-zinc-800" />
        <div className="absolute inset-x-0 bottom-2 mx-auto h-16 w-24 rotate-45 skew-x-12 rounded bg-zinc-850 bg-zinc-800/60" />
      </div>
    </div>
  )
}
