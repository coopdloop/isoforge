/**
 * Session-scoped UI state.
 *
 * Server state (history, themes, versions) belongs to react-query; this holds only
 * what the server does not own: the live scene pushed over WebSocket and the chat
 * transcript, which exists per browser session rather than being persisted.
 */

import { create } from 'zustand'

import type { Scene } from '@/iso/geometry'
import type { ValidationResult } from '@/lib/api'

export interface ChatEntry {
  role: 'user' | 'assistant' | 'error'
  text: string
  version?: number
  summary?: string
  isFullScene?: boolean
  repairs?: number
  errors?: ValidationResult['errors']
}

interface SessionState {
  projectId: string | null
  scene: Scene | null
  version: number
  entries: ChatEntry[]

  setProjectId: (id: string) => void
  setScene: (scene: Scene) => void
  setVersion: (version: number) => void
  addEntry: (entry: ChatEntry) => void
  reset: () => void
}

export const useSessionStore = create<SessionState>((set) => ({
  projectId: null,
  scene: null,
  version: 0,
  entries: [],

  setProjectId: (id) => set({ projectId: id }),
  setScene: (scene) => set({ scene }),
  setVersion: (version) => set({ version }),
  addEntry: (entry) => set((state) => ({ entries: [...state.entries, entry] })),
  reset: () => set({ scene: null, version: 0, entries: [] }),
}))
