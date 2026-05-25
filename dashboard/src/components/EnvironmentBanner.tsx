import { useQuery } from '@tanstack/react-query'

type HealthResponse = {
  app: string
  environment: 'demo' | 'production'
  status: string
}

/**
 * Surfaces the backend's reported environment as a persistent badge.
 * Production is highlighted because real money is on the line — the user should never
 * be able to forget which Kalshi API the app is talking to.
 */
export default function EnvironmentBanner() {
  const { data, isError, isPending } = useQuery<HealthResponse>({
    queryKey: ['root'],
    queryFn: async () => {
      const res = await fetch('/')
      if (!res.ok) throw new Error(`backend: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })

  if (isPending) {
    return <Pill className="border-border text-text-muted">connecting...</Pill>
  }

  if (isError || !data) {
    return <Pill className="border-loss text-loss">backend offline</Pill>
  }

  if (data.environment === 'production') {
    return <Pill className="border-loss bg-loss text-white">PRODUCTION — REAL MONEY</Pill>
  }

  return <Pill className="border-info text-info">DEMO</Pill>
}

function Pill({ children, className }: { children: React.ReactNode; className: string }) {
  return (
    <span
      className={`rounded-md border px-3 py-1 text-xs font-semibold tracking-wide ${className}`}
    >
      {children}
    </span>
  )
}
