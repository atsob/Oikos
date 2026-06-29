import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { loadSettings } from './lib/settings'
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community'

loadSettings()
import 'ag-grid-community/styles/ag-grid.css'
import 'ag-grid-community/styles/ag-theme-alpine.css'
import './index.css'
import App from './App.tsx'

ModuleRegistry.registerModules([AllCommunityModule])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
