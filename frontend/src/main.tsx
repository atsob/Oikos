import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { loadSettings } from './lib/settings'
import { initPreferences } from './lib/preferences'
import { ModuleRegistry, AllCommunityModule } from 'ag-grid-community'

loadSettings()
// Reconcile local (localStorage) preference cache with the server in the
// background — renders immediately from the local cache, then re-renders
// reactively (via subscribePref) once the authoritative server values arrive.
initPreferences()
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
