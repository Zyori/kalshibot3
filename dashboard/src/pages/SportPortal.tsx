import { useParams } from 'react-router'

export default function SportPortal() {
  const { slug } = useParams<{ slug: string }>()

  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text capitalize">{slug ?? 'sport'}</h2>
        <p className="mt-1 text-sm text-text-muted">
          Sport portal scaffolding. Real sections (markets, suggestions, positions, news, history)
          land in Phase 2+.
        </p>
      </header>

      <PlaceholderGrid />
    </div>
  )
}

function PlaceholderGrid() {
  const sections = [
    'Suggested Bets',
    'Live Games',
    'Markets',
    'Open Positions',
    'News',
    'History',
  ]
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {sections.map((title) => (
        <div
          key={title}
          className="rounded-lg border border-border bg-bg-card p-4"
        >
          <h3 className="text-sm font-semibold text-text">{title}</h3>
          <p className="mt-2 text-xs text-text-muted">Coming soon.</p>
        </div>
      ))}
    </div>
  )
}
