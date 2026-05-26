import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Production builds. nginx serves dashboard/dist/ at https://lutz.bot and
// proxies /api/* and /ws/* to the FastAPI backend on 127.0.0.1:8000, so the
// app uses same-origin relative URLs and there's no CORS or proxy plumbing
// to configure here. `npm run watch` rebuilds dist/ on every save.
//
// `npm run dev` (the default Vite dev server with HMR) still works for local
// debugging from a workstation; it's just not what start.sh runs.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
  },
})
