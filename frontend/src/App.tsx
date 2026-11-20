import { Navigate, Route, Routes } from 'react-router-dom'
import { AppHeader } from './components/AppHeader'
import AutomationPage from './pages/AutomationPage'
import ChatPage from './pages/ChatPage'
import DocumentsPage from './pages/DocumentsPage'
import KnowledgePage from './pages/KnowledgePage'
import QuotesPage from './pages/QuotesPage'
import ScorecardPage from './pages/ScorecardPage'
import WorkbenchPage from './pages/WorkbenchPage'

function StaffPage({ children }: { children: React.ReactNode }) {
  return <><AppHeader />{children}</>
}

export default function App() {
  return (
    <div className="app-shell">
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/workbench" element={<StaffPage><WorkbenchPage /></StaffPage>} />
        <Route path="/kb" element={<StaffPage><KnowledgePage /></StaffPage>} />
        <Route path="/quotes" element={<StaffPage><QuotesPage /></StaffPage>} />
        <Route path="/documents" element={<StaffPage><DocumentsPage /></StaffPage>} />
        <Route path="/evals" element={<StaffPage><ScorecardPage /></StaffPage>} />
        <Route path="/automation" element={<StaffPage><AutomationPage /></StaffPage>} />
        <Route path="*" element={<Navigate to="/workbench" replace />} />
      </Routes>
    </div>
  )
}
