import { useQuery } from '@tanstack/react-query'

type GlossaryItem = {
  name: string
  definition: string
  placeholder: boolean
}

type GlossarySection = {
  title: string
  intro: string
  items: GlossaryItem[]
}

type GlossaryResponse = {
  sections: GlossarySection[]
  path: string
}

export default function Settings() {
  return (
    <div className="space-y-6">
      <header>
        <h2 className="text-lg font-semibold text-text">Settings</h2>
        <p className="mt-1 text-sm text-text-muted">
          Bankroll, API status, preferences. Wired up alongside config endpoints in Phase 1.
        </p>
      </header>

      <PartnerSection />
      <GlossarySection />
    </div>
  )
}

// What the AI partner (LUTZ) sees, and how to summon him. Static reference —
// keep in sync with /partner/context (the feeds) and .claude/skills/lutz-partner
// (the trigger) if either changes.
const PARTNER_FEEDS: { name: string; detail: string }[] = [
  { name: 'Open positions', detail: 'side, size, avg entry, unrealized P&L %, and a recent price trajectory per market' },
  { name: 'Recent trades', detail: 'your last 100 bets — strategy, price, result' },
  { name: 'History stats', detail: 'overall win-rate + net P&L, broken down per strategy (so LUTZ catches repeating patterns)' },
  { name: 'Bankroll', detail: 'current available balance' },
  { name: 'Run of play (per game)', detail: 'score, clock, shots, shots on target, possession, corners, cards, saves, penalties + a per-shot stream (quality + location)' },
  { name: 'World Cup news (per game)', detail: 'recent headlines tagged to the two teams — injuries, lineups, suspensions' },
]

function PartnerSection() {
  return (
    <section className="rounded-lg border border-border bg-bg-card">
      <header className="border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold text-text">The AI partner (LUTZ)</h3>
        <p className="mt-0.5 text-xs text-text-muted">
          LUTZ is a Claude Code terminal session that reads your live state and
          stages trade ideas as amber cards you confirm here. He never places
          orders — every trade is yours to confirm.
        </p>
      </header>

      <div className="grid gap-4 p-4 md:grid-cols-2">
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
            What he sees
          </h4>
          <ul className="space-y-1.5">
            {PARTNER_FEEDS.map((f) => (
              <li key={f.name} className="text-xs leading-snug">
                <span className="font-medium text-text">{f.name}</span>
                <span className="text-text-muted"> — {f.detail}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-text-muted">
            He also reads your strategy docs (global + soccer principles, the tag
            glossary below) at the start of every session — plus your{' '}
            <span className="font-medium text-text">2026 World Cup logbook</span>,
            your per-team scouting notes he reads before a game and updates when
            you ask (“note that…”).
          </p>
        </div>

        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
            How to summon him
          </h4>
          <ol className="space-y-1.5 text-xs">
            <li className="flex gap-2">
              <span className="font-mono font-semibold text-action">1</span>
              <span className="text-text-muted">
                Open a terminal in the repo and start Claude Code:{' '}
                <code className="rounded bg-bg px-1 py-0.5 font-mono text-[11px] text-text">claude</code>
              </span>
            </li>
            <li className="flex gap-2">
              <span className="font-mono font-semibold text-action">2</span>
              <span className="text-text-muted">
                Invoke the skill — e.g.{' '}
                <span className="text-text">“use the lutz-partner skill, give me a read on [game]”</span>
              </span>
            </li>
            <li className="flex gap-2">
              <span className="font-mono font-semibold text-action">3</span>
              <span className="text-text-muted">
                He pulls fresh state, grounds in your docs, and stages any call as
                an amber card here. <code className="rounded bg-bg px-1 py-0.5 font-mono text-[11px] text-text">/clear</code> or a new session = a clean read.
              </span>
            </li>
          </ol>
          <p className="mt-2 text-[11px] text-text-muted">
            The backend must be running (he reads{' '}
            <code className="rounded bg-bg px-1 py-0.5 font-mono text-[11px] text-text">127.0.0.1:8000</code>).
          </p>
        </div>
      </div>
    </section>
  )
}

function GlossarySection() {
  const q = useQuery<GlossaryResponse>({
    queryKey: ['glossary'],
    queryFn: async () => {
      const res = await fetch('/api/settings/glossary')
      if (!res.ok) throw new Error(`/api/settings/glossary: ${res.status}`)
      return res.json()
    },
    staleTime: 60_000,
  })

  return (
    <section className="rounded-lg border border-border bg-bg-card">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-text">Tag definitions</h3>
          <p className="mt-0.5 text-xs text-text-muted">
            What each tag means to you. The AI partner reads this to ground its
            suggestions. Edit by hand —{' '}
            <code className="rounded bg-bg px-1 py-0.5 font-mono text-[11px]">
              {q.data?.path ?? 'docs/ai-context/strategy-glossary.md'}
            </code>
          </p>
        </div>
      </header>

      <div className="p-4">
        {q.isPending && (
          <div className="space-y-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <div
                key={i}
                className="h-24 animate-pulse rounded-md border border-border bg-bg"
              />
            ))}
          </div>
        )}

        {q.isError && (
          <p className="text-sm text-loss">
            Couldn't load glossary: {(q.error as Error).message}
          </p>
        )}

        {q.data && (
          <div className="grid gap-6 lg:grid-cols-2">
            {q.data.sections.map((section) => (
              <GlossaryCard key={section.title} section={section} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function GlossaryCard({ section }: { section: GlossarySection }) {
  const filled = section.items.filter((i) => !i.placeholder).length
  return (
    <div className="rounded-md border border-border bg-bg p-3">
      <div className="mb-2 flex items-baseline justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-text">
          {section.title}
        </h4>
        <span className="font-mono text-[10px] text-text-muted">
          {filled}/{section.items.length} defined
        </span>
      </div>
      {section.intro && (
        <p className="mb-2 text-[11px] leading-snug text-text-muted">{section.intro}</p>
      )}
      <dl className="space-y-1.5">
        {section.items.map((item) => (
          <div key={item.name} className="text-xs leading-snug">
            <dt className="inline font-mono text-action">{item.name}</dt>
            <dd
              className={`ml-1 inline ${
                item.placeholder ? 'italic text-text-muted/70' : 'text-text'
              }`}
            >
              — {item.placeholder ? 'not yet defined' : item.definition}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
