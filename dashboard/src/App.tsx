import { Navigate, Route, Routes, useParams } from 'react-router'

import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import EventView from './pages/EventView'
import Futures from './pages/Futures'
import SportPortal from './pages/SportPortal'
import Ledger from './pages/Ledger'
import Combos from './pages/Combos'
import Analysis from './pages/Analysis'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="event/:eventTicker" element={<EventView />} />
        {/* Back-compat: old /market/{ticker} bookmarks land on the event
            page with the right tab pre-selected. Tickers are
            EVENT-OUTCOME shape (drop the trailing -OUTCOME segment to
            get the event ticker). */}
        <Route path="market/:ticker" element={<LegacyMarketRedirect />} />
        <Route path="sport/:slug" element={<SportPortal />} />
        <Route path="futures" element={<Futures />} />
        <Route path="ledger" element={<Ledger />} />
        <Route path="combos" element={<Combos />} />
        {/* Analysis is a dev-only placeholder until the exit post-mortem UI
            ships — gated to match its nav tab so the dead route doesn't reach
            production. */}
        {import.meta.env.DEV && <Route path="analysis" element={<Analysis />} />}
        <Route path="settings" element={<Settings />} />
      </Route>
    </Routes>
  )
}

function LegacyMarketRedirect() {
  const { ticker = '' } = useParams<{ ticker: string }>()
  const decoded = decodeURIComponent(ticker)
  const lastDash = decoded.lastIndexOf('-')
  const eventTicker = lastDash > 0 ? decoded.slice(0, lastDash) : decoded
  return (
    <Navigate
      to={`/event/${encodeURIComponent(eventTicker)}?market=${encodeURIComponent(decoded)}`}
      replace
    />
  )
}
