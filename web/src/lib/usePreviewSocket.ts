/**
 * Live preview socket with exponential-backoff reconnect.
 *
 * The gateway pushes the current scene on connect, so a dropped socket recovers fully
 * without a page reload: reconnecting resyncs state rather than just resuming the feed.
 */

import { useEffect, useRef, useState } from 'react'

import type { Scene } from '@/iso/geometry'

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'offline'

export interface PreviewEvent {
  type: 'connected' | 'scene.updated' | 'export.complete' | 'validation.failed' | 'session.ended'
  session_id?: string
  payload?: {
    scene?: Scene
    version?: number
    scene_hash?: string
    summary?: string
    project_id?: string
    is_full_scene?: boolean
    [key: string]: unknown
  }
  timestamp: string
}

const MAX_BACKOFF_MS = 15_000
const BASE_BACKOFF_MS = 500

export function usePreviewSocket(
  sessionId: string | undefined,
  onEvent: (event: PreviewEvent) => void,
): ConnectionState {
  const [state, setState] = useState<ConnectionState>('connecting')
  const handlerRef = useRef(onEvent)
  handlerRef.current = onEvent

  useEffect(() => {
    if (!sessionId) {
      setState('offline')
      return
    }

    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let cancelled = false

    const connect = () => {
      if (cancelled) return
      setState(attempt === 0 ? 'connecting' : 'reconnecting')

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/preview/${sessionId}`)

      socket.onopen = () => {
        attempt = 0
        setState('connected')
      }

      socket.onmessage = (raw) => {
        try {
          handlerRef.current(JSON.parse(raw.data) as PreviewEvent)
        } catch {
          // A malformed frame must not tear down a working connection.
        }
      }

      socket.onclose = () => {
        if (cancelled) return
        setState('reconnecting')
        const delay = Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS)
        attempt += 1
        retryTimer = setTimeout(connect, delay)
      }

      socket.onerror = () => socket?.close()
    }

    connect()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
      // Clear onclose first so teardown does not schedule a reconnect.
      if (socket) {
        socket.onclose = null
        socket.close()
      }
    }
  }, [sessionId])

  return state
}
