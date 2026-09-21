/**
 * Theme library: builtin palettes plus anything the user has saved.
 *
 * Each palette previews on the same sample scene so they are genuinely comparable,
 * rather than shown as abstract swatch rows.
 */

import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { IsoPreviewCanvas } from '@/components/IsoPreviewCanvas'
import { pushToast } from '@/components/chrome'
import { ApiError, api, type Theme } from '@/lib/api'
import type { Scene } from '@/iso/geometry'

/** A small scene that exercises three tones and a floating accent. */
function sampleScene(colors: Record<string, string>): Scene {
  const names = Object.keys(colors)
  const base = names[0] ?? 'base'
  const accent = names[1] ?? base
  const deep = names[2] ?? base

  return {
    isodsl_version: '1.0.0',
    canvas: { width: 240, height: 240, background: null, padding: 0.1 },
    grid: { w: 4, d: 4, h: 4, cell: 28 },
    camera: { projection: 'isometric', fit: true },
    palette: { colors },
    shading: { enabled: true, top: 0.12, left: -0.14, right: -0.28 },
    shapes: [
      {
        id: 'base',
        type: 'cube',
        at: { x: 0, y: 0, z: 0 },
        size: { x: 2, y: 2, z: 0.5 },
        fill: `@palette.${deep}`,
      },
      {
        id: 'mid',
        type: 'cube',
        at: { x: 0, y: 0, z: 1 },
        size: { x: 1.5, y: 1.5, z: 1 },
        fill: `@palette.${base}`,
      },
      {
        id: 'top',
        type: 'cube',
        at: { x: 2, y: 2, z: 1 },
        size: { x: 1, y: 1, z: 1 },
        fill: `@palette.${accent}`,
      },
    ],
  }
}

export function Themes() {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<string | null>(null)

  const themes = useQuery({ queryKey: ['themes'], queryFn: api.listThemes })

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteTheme(id),
    onSuccess: () => {
      pushToast('theme deleted', 'success')
      queryClient.invalidateQueries({ queryKey: ['themes'] })
    },
    onError: (error: ApiError) => pushToast(error.message, 'error'),
    onSettled: () => setPending(null),
  })

  return (
    <div className="min-h-screen bg-zinc-950 px-8 py-12 text-zinc-100">
      <div className="mx-auto max-w-5xl">
        <header className="mb-8 flex items-baseline gap-3">
          <Link to="/" className="text-sm text-zinc-400 hover:text-white">
            IsoForge
          </Link>
          <span className="text-zinc-700">/</span>
          <h1 className="text-lg tracking-tight">themes</h1>
          <p className="ml-auto text-xs text-zinc-600">
            save one from the CLI:{' '}
            <code className="rounded bg-zinc-900 px-1.5 py-0.5 font-mono">
              isoforge theme save name
            </code>
          </p>
        </header>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(themes.data ?? []).map((theme) => (
            <ThemeCard
              key={theme.id}
              theme={theme}
              busy={pending === theme.id}
              onDelete={() => {
                setPending(theme.id)
                remove.mutate(theme.id)
              }}
            />
          ))}
        </div>

        {themes.data?.length === 0 && (
          <p className="py-12 text-center text-sm text-zinc-600">no themes yet</p>
        )}
      </div>
    </div>
  )
}

function ThemeCard({
  theme,
  busy,
  onDelete,
}: {
  theme: Theme
  busy: boolean
  onDelete: () => void
}) {
  const doc = typeof theme.theme === 'string' ? JSON.parse(theme.theme) : theme.theme
  const colors: Record<string, string> = doc?.colors ?? {}
  const scene = useMemo(() => sampleScene(colors), [theme.id])

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
      <div className="mb-3 flex aspect-[4/3] items-center justify-center rounded bg-zinc-950/60">
        <IsoPreviewCanvas scene={scene} className="h-full w-full" />
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm text-zinc-200">{theme.name}</span>
        {theme.is_builtin ? (
          <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-zinc-500">
            builtin
          </span>
        ) : (
          <button
            onClick={onDelete}
            disabled={busy}
            className="ml-auto text-xs text-zinc-600 hover:text-rose-400 disabled:opacity-40"
          >
            delete
          </button>
        )}
      </div>

      {doc?.description && (
        <p className="mt-1 text-xs leading-snug text-zinc-500">{doc.description}</p>
      )}

      <div className="mt-2 flex gap-1">
        {Object.entries(colors).map(([name, value]) => (
          <span
            key={name}
            title={`${name}  ${value}`}
            className="h-4 flex-1 rounded-sm ring-1 ring-white/10"
            style={{ backgroundColor: String(value).slice(0, 7) }}
          />
        ))}
      </div>
    </div>
  )
}
