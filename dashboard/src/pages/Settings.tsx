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

      <GlossarySection />
    </div>
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
