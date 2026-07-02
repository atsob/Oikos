import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import fs from 'fs'
import path from 'path'
import { execSync } from 'child_process'

function gitInfo() {
  try {
    const run = (cmd: string) => execSync(cmd, { cwd: __dirname }).toString().trim()
    const hash = run('git rev-parse --short HEAD')
    const date = run('git log -1 --format=%cd --date=short')
    const dirty = run('git status --porcelain').length > 0
    return { hash: dirty ? `${hash}-dirty` : hash, date }
  } catch {
    return { hash: 'unknown', date: '' }
  }
}
const GIT_INFO = gitInfo()

export default defineConfig({
  define: {
    __GIT_HASH__: JSON.stringify(GIT_INFO.hash),
    __GIT_DATE__: JSON.stringify(GIT_INFO.date),
  },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'icons.svg', 'icon-192.png', 'icon-512.png'],
      devOptions: {
        enabled: true,
      },
      manifest: {
        name: 'Oikos',
        short_name: 'Oikos',
        description: 'Personal Finance Manager',
        theme_color: '#1e40af',
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
    alias: { '@': path.resolve(__dirname, './src') },
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
    https: {
      key:  fs.readFileSync(path.resolve(__dirname, 'ssl/key.pem')),
      cert: fs.readFileSync(path.resolve(__dirname, 'ssl/cert.pem')),
    },
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
