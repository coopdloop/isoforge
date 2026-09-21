/**
 * Gateway client.
 *
 * The browser talks only to iso_gateway (ADR-002); it never reaches the Python
 * services directly.
 */

import type { Scene } from '@/iso/geometry'

export interface Session {
  id: string
  project_id: string
  conversation_id: string
  title?: string
  created_at: string
  last_seen_at: string
}

export interface HistoryEntry {
  id: string
  version_number: number
  parent_version_id?: string
  scene_hash: string
  change_summary?: string
  created_by?: string
  created_at: string
  is_current: boolean
  shape_count: number
}

export interface SceneVersion {
  id: string
  project_id: string
  version_number: number
  scene: Scene
  patch?: PatchOp[]
  scene_hash: string
  change_summary?: string
  created_at: string
}

export interface PatchOp {
  op: 'add' | 'remove' | 'replace' | 'move' | 'copy' | 'test'
  path: string
  value?: unknown
  from?: string
}

export interface DiffResult {
  from_version: number
  to_version: number
  from_hash: string
  to_hash: string
  changed: boolean
  patch: PatchOp[] | string
  summary: { added: number; removed: number; replaced: number; moved: number; paths: string[] }
}

export interface TurnResult {
  reply: string
  changed: boolean
  is_full_scene?: boolean
  summary?: string
  version?: number
  scene?: Scene
  scene_hash?: string
  repair_attempts?: number
  theme_saved?: string
}

export interface Theme {
  id: string
  name: string
  is_builtin: boolean
  theme: { name: string; description?: string; colors: Record<string, string> }
  created_at: string
}

export interface Project {
  id: string
  name: string
  description?: string
  current_version_number: number
  version_count: number
  created_at: string
  updated_at: string
}

export interface ValidationResult {
  valid: boolean
  errors: Array<{ code: string; message: string; path?: string; shape_id?: string; remedy?: string }>
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** IsoDSL errors, when the failure was a rejected design rather than a transport fault. */
  get validationErrors(): ValidationResult['errors'] {
    const detail = this.payload.detail as Record<string, unknown> | undefined
    if (detail?.errors) return detail.errors as ValidationResult['errors']
    return (this.payload.errors as ValidationResult['errors']) ?? []
  }

  get hint(): string {
    const detail = this.payload.detail as Record<string, unknown> | undefined
    return (detail?.hint as string) ?? (this.payload.hint as string) ?? ''
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch (cause) {
    throw new ApiError('cannot reach the local gateway; is `isoforge chat` running?', 0, {})
  }

  if (!response.ok) {
    let payload: Record<string, unknown> = {}
    try {
      payload = await response.json()
    } catch {
      payload = { error: response.statusText }
    }
    const raw = payload.error ?? payload.detail ?? response.statusText
    const message = typeof raw === 'string' ? raw : ((raw as Record<string, string>)?.error ?? 'request failed')
    throw new ApiError(message, response.status, payload)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Record<string, unknown>>('/health'),

  listProjects: () => request<{ projects: Project[] }>('/projects').then((r) => r.projects),

  listSessions: () => request<{ sessions: Session[] }>('/sessions').then((r) => r.sessions),

  createSession: (body: { name?: string; project_id?: string; scene?: Scene }) =>
    request<Session>('/sessions', { method: 'POST', body: JSON.stringify(body) }),

  getSession: (id: string) =>
    request<{
      session: Session
      scene?: Scene
      version?: number
      scene_hash?: string
      preview_clients: number
    }>(`/sessions/${id}`),

  endSession: (id: string) => request<void>(`/sessions/${id}`, { method: 'DELETE' }),

  sendMessage: (sessionId: string, message: string) =>
    request<TurnResult>(`/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ message }),
    }),

  getScene: (projectId: string) => request<SceneVersion>(`/scenes/${projectId}`),

  history: (projectId: string, limit = 100) =>
    request<{ history: HistoryEntry[]; count: number }>(
      `/scenes/${projectId}/history?limit=${limit}`,
    ).then((r) => r.history),

  getVersion: (projectId: string, version: number) =>
    request<SceneVersion>(`/scenes/${projectId}/versions/${version}`),

  revert: (projectId: string, version: number, sessionId: string) =>
    request<SceneVersion>(`/scenes/${projectId}/revert`, {
      method: 'POST',
      body: JSON.stringify({ version, session_id: sessionId }),
    }),

  diff: (projectId: string, from?: number, to?: number) => {
    const params = new URLSearchParams()
    if (from) params.set('from', String(from))
    if (to) params.set('to', String(to))
    const qs = params.toString()
    return request<DiffResult>(`/scenes/${projectId}/diff${qs ? `?${qs}` : ''}`)
  },

  listThemes: () => request<{ themes: Theme[] }>('/themes').then((r) => r.themes),

  saveTheme: (body: { name: string; colors: Record<string, string>; project_id?: string }) =>
    request<Theme>('/themes', { method: 'POST', body: JSON.stringify(body) }),

  deleteTheme: (id: string) => request<void>(`/themes/${id}`, { method: 'DELETE' }),

  validate: (scene: Scene) =>
    request<ValidationResult>('/schema/validate', {
      method: 'POST',
      body: JSON.stringify({ scene }),
    }),

  exportArtifact: (body: {
    session_id?: string
    project_id?: string
    version?: number
    format: string
    options?: Record<string, unknown>
  }) =>
    request<{ export_id: string; download_url: string; filename: string; size_bytes: number }>(
      '/export',
      { method: 'POST', body: JSON.stringify(body) },
    ),
}

/** Normalise a diff patch, which the gateway may return as a JSON string. */
export function patchOps(diff: DiffResult): PatchOp[] {
  if (typeof diff.patch === 'string') {
    try {
      return JSON.parse(diff.patch) as PatchOp[]
    } catch {
      return []
    }
  }
  return diff.patch ?? []
}
