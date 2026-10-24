import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API runs on :8000 (uvicorn backend.api.main:app). The dev server forwards to it,
// so the browser talks to one origin.
const API = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': API,
      '/ws': { target: API.replace('http', 'ws'), ws: true },
    },
  },
})
