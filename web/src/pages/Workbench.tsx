/**
 * The three-pane core experience: chat transcript, live preview, version filmstrip.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { IsoPreviewCanvas } from '@/components/IsoPreviewCanvas'
import { DSLDiffViewer, DiffSummaryBadges } from '@/components/DSLDiffViewer'
import {
  ConnectionStatusIndicator,
  LoadingSkeletonScene,
  PaletteStrip,
  pushToast,
} from '@/components/chrome'
import { ApiError, api, patchOps, type PatchOp } from '@/lib/api'
import { usePreviewSocket, type PreviewEvent } from '@/lib/usePreviewSocket'
import type { Scene } from '@/iso/geometry'
import { useSessionStore, type ChatEntry } from '@/store'

export function Workbench() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [input, setInput] = useState('')
  const [highlightShape, setHighlightShape] = useState<string | null>(null)
  const [showGrid, setShowGrid] = useState(false)
  const [viewingVersion, setViewingVersion] = useState<number | null>(null)
  const transcriptEnd = useRef<HTMLDivElement>(null)

  const {
    scene, setScene, version, setVersion,
    entries, addEntry, projectId, setProjectId, reset,
  } = useSessionStore()

  const session = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => api.getSession(sessionId!),
    enabled: Boolean(sessionId),
    retry: 1,
  })

  useEffect(() => {
    if (!session.data) return
    setProjectId(session.data.session.project_id)
    if (session.data.scene) {
      setScene(session.data.scene)
      setVersion(session.data.version ?? 0)
    }
  }, [session.data, setProjectId, setScene, setVersion])

  // Reset transcript when switching sessions so history never bleeds across.
  useEffect(() => {
    reset()
  }, [sessionId, reset])

  const history = useQuery({
    queryKey: ['history', projectId],
    queryFn: () => api.history(projectId!),
    enabled: Boolean(projectId),
  })

  const onEvent = useCallback(
    (event: PreviewEvent) => {
      if (event.type === 'scene.updated' && event.payload?.scene) {
        // Viewing an older version should not be yanked away by a live update.
        setViewingVersion(null)
        setScene(event.payload.scene)
        setVersion(event.payload.version ?? 0)
        queryClient.invalidateQueries({ queryKey: ['history'] })
      } else if (event.type === 'connected' && event.payload?.scene) {
        setScene(event.payload.scene)
        setVersion(event.payload.version ?? 0)
      } else if (event.type === 'export.complete') {
        pushToast('export ready', 'success')
      } else if (event.type === 'session.ended') {
        pushToast('session ended in the CLI', 'info')
      }
    },
    [queryClient, setScene, setVersion],
  )

  const connection = usePreviewSocket(sessionId, onEvent)

  const sendMessage = useMutation({
    mutationFn: (message: string) => api.sendMessage(sessionId!, message),
    onSuccess: (result, message) => {
      addEntry({ role: 'user', text: message })
      addEntry({
        role: 'assistant',
        text: result.reply,
        version: result.version,
        summary: result.summary,
        isFullScene: result.is_full_scene,
        repairs: result.repair_attempts,
      })
      if (result.theme_saved) pushToast(`saved theme '${result.theme_saved}'`, 'success')
      queryClient.invalidateQueries({ queryKey: ['history'] })
    },
    onError: (error: ApiError, message) => {
      addEntry({ role: 'user', text: message })
      addEntry({
        role: 'error',
        text: error.message,
        errors: error.validationErrors,
      })
    },
  })

  const revert = useMutation({
    mutationFn: (v: number) => api.revert(projectId!, v, sessionId!),
    onSuccess: (result) => {
      pushToast(`reverted to v${result.change_summary?.match(/\d+/)?.[0] ?? '?'}`, 'success')
      setViewingVersion(null)
      queryClient.invalidateQueries({ queryKey: ['history'] })
    },
    onError: (error: ApiError) => pushToast(error.message, 'error'),
  })

  const exportArtifact = useMutation({
    mutationFn: (format: string) =>
      api.exportArtifact({
        session_id: sessionId,
        format,
        options: format === 'png' ? { width: 1024, height: 1024 } : {},
      }),
    onSuccess: (artifact) => {
      window.open(artifact.download_url, '_blank')
      pushToast(`exported ${artifact.filename}`, 'success')
    },
    onError: (error: ApiError) => pushToast(error.message, 'error'),
  })

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [entries.length, sendMessage.isPending])

  const versionScene = useQuery({
    queryKey: ['version', projectId, viewingVersion],
    queryFn: () => api.getVersion(projectId!, viewingVersion!),
    enabled: Boolean(projectId && viewingVersion),
  })

  const diff = useQuery({
    queryKey: ['diff', projectId, version],
    queryFn: () => api.diff(projectId!),
    enabled: Boolean(projectId && version > 1),
  })

  if (session.isError) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 text-center">
        <p className="text-zinc-300">that session is not running</p>
        <p className="max-w-sm text-sm text-zinc-500">
          Sessions live inside a running <code className="text-zinc-400">isoforge chat</code>.
          Start one in your terminal, or pick another below.
        </p>
        <button
          onClick={() => navigate('/')}
          className="rounded-md bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-700"
        >
          back to sessions
        </button>
      </div>
    )
  }

  const displayedScene: Scene | null = viewingVersion ? (versionScene.data?.scene ?? null) : scene
  const isHistoricView = viewingVersion !== null

  return (
    <div className="flex h-screen flex-col bg-zinc-950 text-zinc-100">
      <header className="flex shrink-0 items-center gap-3 border-b border-zinc-800 px-4 py-2">
        <button
          onClick={() => navigate('/')}
          className="text-sm font-medium tracking-tight text-zinc-300 hover:text-white"
        >
          IsoForge
        </button>
        <span className="text-zinc-700">/</span>
        <span className="truncate text-sm text-zinc-400">
          {session.data?.session.title || 'untitled'}
        </span>
        {scene && (
          <PaletteStrip
            colors={scene.palette?.colors ?? {}}
            locked={scene.palette?.locked}
            className="ml-2"
          />
        )}
        <div className="ml-auto flex items-center gap-3">
          <ConnectionStatusIndicator state={connection} />
          <button
            onClick={() => setShowGrid((v) => !v)}
            className={`rounded px-2 py-1 text-xs ${showGrid ? 'bg-zinc-800 text-zinc-200' : 'text-zinc-500 hover:text-zinc-300'}`}
          >
            grid
          </button>
          <div className="flex items-center gap-1">
            {['svg', 'png', 'icon-bundle'].map((format) => (
              <button
                key={format}
                onClick={() => exportArtifact.mutate(format)}
                disabled={!scene || exportArtifact.isPending}
                className="rounded bg-zinc-800 px-2 py-1 text-xs text-zinc-200 hover:bg-zinc-700 disabled:opacity-40"
              >
                {format === 'icon-bundle' ? 'icons' : format}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* chat transcript */}
        <section className="flex w-[380px] shrink-0 flex-col border-r border-zinc-800">
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
            {entries.length === 0 && (
              <div className="pt-8 text-center text-sm text-zinc-500">
                <p className="mb-2 text-zinc-400">Describe the logo you want.</p>
                <p className="text-xs leading-relaxed">
                  Try: “a stack of three cubes forming a step,
                  <br />
                  electric blue with a cyan top”
                </p>
              </div>
            )}
            {entries.map((entry, i) => (
              <ChatBubble key={i} entry={entry} onJump={setViewingVersion} />
            ))}
            {sendMessage.isPending && (
              <div className="flex items-center gap-2 text-sm text-zinc-500">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500" />
                designing…
              </div>
            )}
            <div ref={transcriptEnd} />
          </div>

          <form
            className="shrink-0 border-t border-zinc-800 p-3"
            onSubmit={(e) => {
              e.preventDefault()
              const text = input.trim()
              if (!text || sendMessage.isPending) return
              setInput('')
              sendMessage.mutate(text)
            }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={scene ? 'describe a change…' : 'describe your logo…'}
              disabled={sendMessage.isPending}
              className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm placeholder:text-zinc-600 focus:border-zinc-600 focus:outline-none disabled:opacity-50"
            />
          </form>
        </section>

        {/* preview */}
        <section className="relative flex min-w-0 flex-1 flex-col">
          {isHistoricView && (
            <div className="flex items-center gap-3 border-b border-amber-900/40 bg-amber-950/30 px-4 py-1.5 text-xs text-amber-300">
              viewing v{viewingVersion}
              <button
                onClick={() => revert.mutate(viewingVersion)}
                className="rounded bg-amber-900/60 px-2 py-0.5 hover:bg-amber-900"
              >
                restore this version
              </button>
              <button
                onClick={() => setViewingVersion(null)}
                className="ml-auto text-amber-400/70 hover:text-amber-300"
              >
                back to latest
              </button>
            </div>
          )}
          <div className="flex min-h-0 flex-1 items-center justify-center p-8">
            {session.isLoading ? (
              <LoadingSkeletonScene />
            ) : (
              <IsoPreviewCanvas
                scene={displayedScene}
                className="h-full max-h-[70vh] w-full text-zinc-400"
                highlightShapeId={highlightShape}
                showGrid={showGrid}
              />
            )}
          </div>

          {diff.data && diff.data.changed && !isHistoricView && (
            <div className="shrink-0 border-t border-zinc-800 p-3">
              <div className="mb-1.5 flex items-center gap-2 text-xs text-zinc-500">
                <span>
                  v{diff.data.from_version} → v{diff.data.to_version}
                </span>
                <DiffSummaryBadges summary={diff.data.summary} />
              </div>
              <DSLDiffViewer
                ops={patchOps(diff.data).slice(0, 8)}
                onHoverShape={setHighlightShape}
              />
            </div>
          )}
        </section>

        {/* version filmstrip */}
        <aside className="flex w-[220px] shrink-0 flex-col border-l border-zinc-800">
          <h2 className="shrink-0 px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">
            versions
          </h2>
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
            {(history.data ?? []).map((entry) => {
              const active = viewingVersion
                ? entry.version_number === viewingVersion
                : entry.is_current
              return (
                <button
                  key={entry.id}
                  onClick={() =>
                    setViewingVersion(entry.is_current ? null : entry.version_number)
                  }
                  className={`mb-1 block w-full rounded-md px-2 py-1.5 text-left text-xs transition ${
                    active ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-400 hover:bg-zinc-900'
                  }`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-zinc-500">v{entry.version_number}</span>
                    {entry.is_current && (
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    )}
                    <span className="ml-auto text-[10px] text-zinc-600">
                      {entry.shape_count} shapes
                    </span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 leading-snug">
                    {entry.change_summary || '—'}
                  </p>
                </button>
              )
            })}
            {history.data?.length === 0 && (
              <p className="px-2 py-4 text-center text-xs text-zinc-600">no versions yet</p>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}

function ChatBubble({
  entry,
  onJump,
}: {
  entry: ChatEntry
  onJump: (version: number) => void
}) {
  if (entry.role === 'user') {
    return (
      <div className="ml-6 rounded-lg bg-zinc-800/70 px-3 py-2 text-sm text-zinc-100">
        {entry.text}
      </div>
    )
  }

  if (entry.role === 'error') {
    return (
      <div className="rounded-lg border border-rose-900/50 bg-rose-950/30 px-3 py-2 text-sm">
        <p className="text-rose-300">{entry.text}</p>
        {entry.errors?.slice(0, 3).map((err, i) => (
          <p key={i} className="mt-1 font-mono text-xs text-rose-400/80">
            {err.code} {err.message}
          </p>
        ))}
      </div>
    )
  }

  return (
    <div className="px-1 text-sm">
      <p className="text-zinc-200">{entry.text}</p>
      {entry.version != null && (
        <button
          onClick={() => onJump(entry.version!)}
          className="mt-1 flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-300"
        >
          <span className="font-mono">v{entry.version}</span>
          <span>{entry.isFullScene ? 'rewrote' : 'patched'}</span>
          {entry.repairs ? (
            <span className="text-amber-500/80">
              after {entry.repairs} correction{entry.repairs > 1 ? 's' : ''}
            </span>
          ) : null}
        </button>
      )}
    </div>
  )
}

export type { PatchOp }
