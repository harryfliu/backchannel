import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { boot } from './state/store'
import './styles.css'

const root = document.getElementById('root')
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  void boot(new URLSearchParams(window.location.search).get('c') ?? 'grp-family')
}
