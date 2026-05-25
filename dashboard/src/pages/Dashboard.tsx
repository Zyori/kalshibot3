import { Link } from 'react-router'

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <PageHeading
        title="Overview"
        subtitle="Cross-sport summary lands here. For now: jump to a sport portal."
      />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <SportCard slug="soccer" name="Soccer" status="primary" />
      </div>
    </div>
  )
}

function PageHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h2 className="text-lg font-semibold text-text">{title}</h2>
      {subtitle && <p className="mt-1 text-sm text-text-muted">{subtitle}</p>}
    </div>
  )
}

function SportCard({
  slug,
  name,
  status,
}: {
  slug: string
  name: string
  status: 'primary' | 'future'
}) {
  return (
    <Link
      to={`/sport/${slug}`}
      className="group block rounded-lg border border-border bg-bg-card p-4 transition-colors hover:bg-bg-hover"
    >
      <div className="flex items-baseline justify-between">
        <h3 className="text-base font-semibold text-text">{name}</h3>
        <span
          className={
            status === 'primary'
              ? 'text-xs text-gain'
              : 'text-xs text-text-muted'
          }
        >
          {status === 'primary' ? 'active' : 'future'}
        </span>
      </div>
      <p className="mt-2 text-xs text-text-muted">
        Open the portal for news, markets, suggestions, and live positions.
      </p>
    </Link>
  )
}
