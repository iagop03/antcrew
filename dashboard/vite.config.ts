import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // The SPA is served at /ui/ from FastAPI
  base: '/ui/',
  build: {
    // Output directly into the Python package so FastAPI can serve it
    outDir: '../antcrew/static',
    emptyOutDir: true,
  },
  server: {
    // Proxy API calls to the FastAPI backend during development
    proxy: {
      '/run':   'http://localhost:8000',
      '/runs':  'http://localhost:8000',
      '/eval':  'http://localhost:8000',
      '/evals': 'http://localhost:8000',
    },
  },
})
