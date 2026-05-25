import { useQuery } from '@tanstack/react-query'

type HealthResponse = {
  app: string
  environment: 'demo' | 'production'
  status: string
  db: { ok: boolean; error: string | null }
  kalshi: {
    ok: boolean
    checked_at: string | null
    balance_cents: number | null
    error: string | null
  }
}

/**
 * Surfaces the backend's reported environment plus subsystem health.
 *
 * The environment pill is the load-bearing widget: PRODUCTION renders red so
 * the user can never forget real money is on the line. A separate pill shows
 * Kalshi auth status — if Kalshi auth is down, trading is broken even though
 * the rest of the app keeps working.
 */
export default function EnvironmentBanner() {
  const { data, isError, isPending } = useQuery<HealthResponse>({
    queryKey: ['health'],
    queryFn: async () => {
      const res = await fetch('/api/health')
      if (!res.ok) throw new Error(`backend: ${res.status}`)
      return res.json()
    },
    refetchInterval: 15_000,
  })

  if (isPending) {
    return (
      <div className="flex items-center gap-2">
        <Pill className="border-border text-text-muted">connecting…</Pill>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="flex items-center gap-2">
        <Pill className="border-loss text-loss">backend offline</Pill>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2">
      {data.environment === 'production' ? (
        <Pill className="border-loss bg-loss text-white">PRODUCTION — REAL MONEY</Pill>
      ) : (
        <Pill className="border-info text-info">DEMO</Pill>
      )}
      <KalshiPill kalshi={data.kalshi} />
    </div>
  )
}

function KalshiPill({ kalshi }: { kalshi: HealthResponse['kalshi'] }) {
  if (kalshi.ok) {
    const dollars =
      kalshi.balance_cents !== null
        ? `$${(kalshi.balance_cents / 100).toFixed(2)}`
        : '—'
    return (
      <Pill
        className="border-gain text-gain"
        title={kalshi.checked_at ?? undefined}
      >
        Kalshi · {dollars}
      </Pill>
    )
  }

  return (
    <Pill
      className="border-loss text-loss"
      title={kalshi.error ?? 'unknown error'}
    >
      Kalshi auth down
    </Pill>
  )
}

function Pill({
  children,
  className,
  title,
}: {
  children: React.ReactNode
  className: string
  title?: string
}) {
  return (
    <span
      title={title}
      className={`rounded-md border px-3 py-1 text-xs font-semibold tracking-wide ${className}`}
    >
      {children}
    </span>
  )
}
