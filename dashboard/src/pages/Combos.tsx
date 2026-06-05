import { useState } from 'react'

import ComboBuilder from '../components/combos/ComboBuilder'
import ComboLogForm from '../components/combos/ComboLogForm'

/**
 * Combos — parlays in one place. Build and place a combo on Kalshi (the builder),
 * or log one you placed on kalshi.com (the log form). Either way it lands in the
 * ledger and settles on its own.
 */
export default function Combos() {
  const [tab, setTab] = useState<'build' | 'log'>('build')
  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text">Combos</h2>
        <p className="mt-1 text-sm text-text-muted">
          Parlays — your bread and butter. Build one here, or log one you placed
          on kalshi.com.
        </p>
      </header>

      <div className="flex gap-1 border-b border-border">
        <Tab active={tab === 'build'} onClick={() => setTab('build')}>
          Build &amp; place
        </Tab>
        <Tab active={tab === 'log'} onClick={() => setTab('log')}>
          Log existing
        </Tab>
      </div>

      {/* Builder uses the full page width; the log form stays narrow. */}
      {tab === 'build' ? (
        <ComboBuilder />
      ) : (
        <div className="max-w-2xl">
          <ComboLogForm />
        </div>
      )}
    </div>
  )
}

function Tab({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-px border-b-2 px-3 py-2 text-sm ${
        active
          ? 'border-action text-text'
          : 'border-transparent text-text-muted hover:text-text'
      }`}
    >
      {children}
    </button>
  )
}
