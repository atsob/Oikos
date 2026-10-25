import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import fs from 'fs'
import path from 'path'
import { execSync } from 'child_process'

function gitInfo() {
  // Docker builds exclude .git from the build context (see .dockerignore), so `git`
  // isn't available inside the image — the host supplies the commit via build args
  // (see Dockerfile / docker-compose.yml) instead of us shelling out here.
  if (process.env.GIT_HASH) {
    return { hash: process.env.GIT_HASH, date: process.env.GIT_DATE ?? '' }
  }
  try {
    const run = (cmd: string) => execSync(cmd, { cwd: import.meta.dirname }).toString().trim()
    const hash = run('git rev-parse --short HEAD')
    const date = run('git log -1 --format=%cd --date=short')
    const dirty = run('git status --porcelain').length > 0
    return { hash: dirty ? `${hash}-dirty` : hash, date }
  } catch {
    return { hash: 'unknown', date: '' }
  }
}
const GIT_INFO = gitInfo()

// The release version, vYYYY-MM-DD.HF (HF = the hot fix of that date, from 0). A Docker / release build is told it (APP_VERSION build arg, set from
// the git tag); a build from a checkout reads the tag on the current commit, if there is one. Empty otherwise — the footer then shows the commit.
const VERSION_RE = /^v\d{4}-\d{2}-\d{2}\.\d+$/
function appVersion(): string {
  const given = (process.env.APP_VERSION ?? '').trim()
  if (given) return VERSION_RE.test(given) ? given : ''
  try {
    const tags = execSync('git tag --points-at HEAD', { cwd: import.meta.dirname }).toString().split(/\r?\n/).map(t => t.trim())
    return tags.filter(t => VERSION_RE.test(t)).sort().pop() ?? ''
  } catch {
    return ''
  }
}
const APP_VERSION = appVersion()

// The dev server's HTTPS certificate. Only the dev server uses it, and the folder is not in Git (a clean checkout — the GitHub release
// build, a Docker build — has no ssl/), so it is read only when both files exist; otherwise the dev server falls back to plain HTTP.
const keyPath = path.resolve(import.meta.dirname, 'ssl/key.pem')
const certPath = path.resolve(import.meta.dirname, 'ssl/cert.pem')
const devHttps = fs.existsSync(keyPath) && fs.existsSync(certPath)
  ? { key: fs.readFileSync(keyPath), cert: fs.readFileSync(certPath) }
  : undefined

export default defineConfig({
  define: {
    __GIT_HASH__: JSON.stringify(GIT_INFO.hash),
    __GIT_DATE__: JSON.stringify(GIT_INFO.date),
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.png', 'logo.png', 'icons.svg', 'icon-192.png', 'icon-512.png'],
      devOptions: {
        enabled: true,
      },
      manifest: {
        name: 'Oikos',
        short_name: 'Oikos',
        description: 'Personal Finance Manager',
        theme_color: '#312682',
        background_color: '#ffffff',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        maximumFileSizeToCacheInBytes: 10 * 1024 * 1024,
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: 'NetworkOnly',
          },
        ],
      },
    }),
  ],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  optimizeDeps: {
    include: ['plotly.js/dist/plotly', 'react-plotly.js'],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('plotly') || id.includes('react-plotly')) return 'vendor-plotly'
          if (id.includes('ag-grid')) return 'vendor-aggrid'
          if (id.includes('@radix-ui')) return 'vendor-radix'
          if (id.includes('@tanstack')) return 'vendor-query'
          if (id.includes('node_modules/react') || id.includes('react-router')) return 'vendor-react'
        },
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    https: devHttps,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
