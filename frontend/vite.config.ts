import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // O maplibre cria o worker com { type: 'module' }.
  worker: { format: 'es' },
})
