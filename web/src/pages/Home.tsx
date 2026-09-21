/**
 * Session launcher.
 *
 * Sessions are owned by a running `isoforge chat` process, so this page is a chooser
 * rather than a creator: it lists what is live and what has been designed before.
 */

import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'

import { IsoPreviewCanvas } from '@/components/IsoPreviewCanvas'
import { pushToast } from '@/components/chrome'
import { ApiError, api } from '@/lib/api'

export function Home() {
  const navigate = useNavigate()
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: api.listSessions })
  const projects = useQuery({ queryKey: ['projects'], queryFn: api.listProjects })

  // Opening a past design starts a session on that project, so the agent resumes with
  // the existing scene in context instead of designing from scratch.
  const resume = useMutation({
    mutationFn: (projectId: string) => api.createSession({ project_id: projectId }),
    onSuccess: (session) => navigate(`/sessions/${session.id}`),
    onError: (error: ApiError) => pushToast(error.message, 'error'),
  })

  const active = sessions.data ?? []
  const designed = (projects.data ?? []).filter((p) => p.version_count > 0)

  return (
    <div className="min-h-screen bg-zinc-950 px-8 py-12 text-zinc-100">
      <div className="mx-auto max-w-4xl">
        <header className="mb-10">
          <h1 className="text-2xl font-medium tracking-tight">IsoForge</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Describe it. Watch it build. Ship the cube.
          </p>
        </header>

        {active.length > 0 && (
          <section className="mb-10">
            <h2 className="mb-3 text-xs uppercase tracking-wide text-zinc-500">
              live sessions
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {active.map((session) => (
                <Link
                  key={session.id}
                  to={`/sessions/${session.id}`}
                  className="group rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 transition hover:border-zinc-600"
                >
                  <div className="flex items-center gap-2">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                    <span className="text-sm text-zinc-200">
                      {session.title || 'untitled'}
                    </span>
                  </div>
                  <p className="mt-1 font-mono text-xs text-zinc-600">
                    {session.id.slice(0, 8)}
                  </p>
                </Link>
              ))}
            </div>
          </section>
        )}

        <section>
          <h2 className="mb-3 text-xs uppercase tracking-wide text-zinc-500">designs</h2>
          {designed.length === 0 ? (
            <EmptyState hasSessions={active.length > 0} />
          ) : (
            <div className="grid gap-3 sm:grid-cols-3">
              {designed.map((project) => {
                const live = active.find((s) => s.project_id === project.id)
                return (
                  <ProjectCard
                    key={project.id}
                    projectId={project.id}
                    name={project.name}
                    versions={project.version_count}
                    busy={resume.isPending && resume.variables === project.id}
                    onOpen={() =>
                      live ? navigate(`/sessions/${live.id}`) : resume.mutate(project.id)
                    }
                  />
                )
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function ProjectCard({
  projectId,
  name,
  versions,
  busy,
  onOpen,
}: {
  projectId: string
  name: string
  versions: number
  busy: boolean
  onOpen: () => void
}) {
  const scene = useQuery({
    queryKey: ['scene', projectId],
    queryFn: () => api.getScene(projectId),
  })

  return (
    <button
      onClick={onOpen}
      disabled={busy}
      className="group rounded-lg border border-zinc-800 bg-zinc-900/30 p-3 text-left transition hover:border-zinc-600 disabled:opacity-50"
    >
      <div className="mb-2 flex aspect-square items-center justify-center rounded bg-zinc-950/50">
        <IsoPreviewCanvas scene={scene.data?.scene} className="h-full w-full p-2" />
      </div>
      <p className="truncate text-sm text-zinc-300">{name}</p>
      <p className="text-xs text-zinc-600">
        {busy ? 'opening…' : `${versions} version${versions === 1 ? '' : 's'}`}
      </p>
    </button>
  )
}

function EmptyState({ hasSessions }: { hasSessions: boolean }) {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 px-6 py-12 text-center">
      <p className="text-sm text-zinc-400">
        {hasSessions ? 'no designs yet' : 'no sessions running'}
      </p>
      <p className="mt-2 text-xs text-zinc-600">
        Start one in your terminal:
        <code className="ml-2 rounded bg-zinc-900 px-2 py-1 font-mono text-zinc-400">
          isoforge chat
        </code>
      </p>
    </div>
  )
}
