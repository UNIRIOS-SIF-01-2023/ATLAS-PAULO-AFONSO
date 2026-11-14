import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { config as maplibreConfig } from 'maplibre-gl'
// `?worker&url` empacota o worker junto com o `maplibre-gl-shared.mjs` que
// ele importa; com `?url` o shared não ia pro build e o worker falhava.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import './index.css'
import App from './App.tsx'

// O maplibre resolve o worker com `new URL(..., import.meta.url)`, que o
// Vite não empacota (dev) nem emite (build) — o mapa nascia sem worker e
// ficava em branco. URL explícita funciona nos dois modos.
maplibreConfig.WORKER_URL = maplibreWorkerUrl

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
