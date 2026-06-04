import { NavLink, Outlet } from 'react-router'

import EnvironmentBanner from './EnvironmentBanner'
import WsIndicator from './WsIndicator'

/**
 * Top-level layout shell. Header with nav + environment banner, content slot below.
 * The OrderPanel and ChatPanel will displace this layout in later chunks — for now
 * the shell is a stable grid we can fill in incrementally.
 */
export default function Layout() {
  return (
    <div className="min-h-screen bg-bg text-text">
      <header className="border-b border-border bg-bg-panel">
        <div className="mx-auto flex max-w-screen-2xl items-center justify-between px-6 py-3">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <h1 className="text-base font-semibold text-text">kalshibot3</h1>
              <WsIndicator />
            </div>
            <nav className="flex gap-1">
              <NavTab to="/" label="Overview" end />
              <NavTab to="/sport/soccer" label="Soccer" />
              <NavTab to="/futures" label="World Cup" />
              <NavTab to="/ledger" label="Ledger" />
              {/* Analysis is a placeholder until the exit post-mortem UI ships;
                  the /analysis route stays registered so it loads in dev. */}
              {import.meta.env.DEV && <NavTab to="/analysis" label="Analysis" />}
              <NavTab to="/settings" label="Settings" />
            </nav>
          </div>
          <EnvironmentBanner />
        </div>
      </header>

      <main className="mx-auto max-w-screen-2xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  )
}

function NavTab({ to, label, end }: { to: string; label: string; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        [
          'rounded-md border px-3 py-1.5 text-xs transition-colors',
          isActive
            ? 'border-accent bg-accent text-white'
            : 'border-border text-text-muted hover:bg-bg-hover hover:text-text',
        ].join(' ')
      }
    >
      {label}
    </NavLink>
  )
}
