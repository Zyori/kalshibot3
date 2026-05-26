import { Route, Routes } from 'react-router'

import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import MarketView from './pages/MarketView'
import SportPortal from './pages/SportPortal'
import Ledger from './pages/Ledger'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="market/:ticker" element={<MarketView />} />
        <Route path="sport/:slug" element={<SportPortal />} />
        <Route path="ledger" element={<Ledger />} />
        <Route path="settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}
