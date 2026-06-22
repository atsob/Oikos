import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
