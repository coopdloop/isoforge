import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { ToastStack } from '@/components/chrome'
import { Home } from '@/pages/Home'
import { Themes } from '@/pages/Themes'
import { Workbench } from '@/pages/Workbench'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Scene updates arrive over WebSocket, so polling would be redundant work.
      refetchOnWindowFocus: false,
      staleTime: 5_000,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/sessions/:sessionId" element={<Workbench />} />
          <Route path="/themes" element={<Themes />} />
          <Route path="*" element={<Home />} />
        </Routes>
      </BrowserRouter>
      <ToastStack />
    </QueryClientProvider>
  </StrictMode>,
)
