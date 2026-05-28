import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Bet } from '../../lib/types'

// Keep these option lists in sync with backend Strategy / BetSource /
// Timing / Confidence enums in src/core/types.py. Drift here only affects
// what's selectable in the UI — the backend will still reject unknown
// values, so a stale list shows up as a 422 on Save, not silent data loss.
const STRATEGY_OPTIONS = [
  'mean_reversion',
  'mean_confirmation',
  'lock_parlay',
  'underdog',
  'moon_parlay',
  'draw_value',
  'live_event',
  'manual',
] as const
const SOURCE_OPTIONS = ['human', 'ai', 'collaborative', 'external'] as const
const TIMING_OPTIONS = ['pre_match', 'live', 'futures'] as const
const CONFIDENCE_OPTIONS = ['low', 'medium', 'high'] as const

export default function BetMetadataForm({
  bet,
  onDone,
}: {
  bet: Bet
  onDone: () => void
}) {
  const qc = useQueryClient()
  const [strategy, setStrategy] = useState(bet.strategy)
  const [source, setSource] = useState(bet.source)
  const [timing, setTiming] = useState(bet.timing)
  const [confidence, setConfidence] = useState(bet.confidence)
  const [tags, setTags] = useState<string[]>(bet.tags ?? [])
  const [tagDraft, setTagDraft] = useState('')
  const [memo, setMemo] = useState(bet.human_reasoning ?? '')

  // Re-seed local state when switching between bets without unmount.
  useEffect(() => {
    setStrategy(bet.strategy)
    setSource(bet.source)
    setTiming(bet.timing)
    setConfidence(bet.confidence)
    setTags(bet.tags ?? [])
    setTagDraft('')
    setMemo(bet.human_reasoning ?? '')
  }, [bet.id])

  const allTags = useQuery<{ tags: string[] }>({
    queryKey: ['ledger_tags'],
    queryFn: async () => {
      const res = await fetch('/api/ledger/tags')
      if (!res.ok) throw new Error(`/api/ledger/tags: ${res.status}`)
      return res.json()
    },
    staleTime: 60_000,
  })

  const suggestions = useMemo(() => {
    const draft = tagDraft.trim().toLowerCase()
    const pool = allTags.data?.tags ?? []
    return pool
      .filter((t) => !tags.includes(t))
      .filter((t) => !draft || t.toLowerCase().includes(draft))
      .slice(0, 8)
  }, [allTags.data, tagDraft, tags])

  const save = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/ledger/${bet.id}/metadata`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          strategy,
          source,
          timing,
          confidence,
          tags,
          human_reasoning: memo.trim() || null,
        }),
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`PATCH /metadata ${res.status}: ${body}`)
      }
      return res.json()
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ledger'] })
      qc.invalidateQueries({ queryKey: ['ledger_stats'] })
      qc.invalidateQueries({ queryKey: ['ledger_tags'] })
      onDone()
    },
  })

  const addTag = (raw: string) => {
    const t = raw.trim()
    if (!t) return
    if (tags.includes(t)) return
    setTags([...tags, t])
    setTagDraft('')
  }

  return (
    <div className="space-y-4 rounded-md border border-action/30 bg-action/5 p-3">
      <ChipGroup
        title="Strategy"
        options={STRATEGY_OPTIONS as readonly string[]}
        value={strategy}
        onChange={setStrategy}
      />
      <ChipGroup
        title="Source"
        options={SOURCE_OPTIONS as readonly string[]}
        value={source}
        onChange={setSource}
      />
      <ChipGroup
        title="Timing"
        options={TIMING_OPTIONS as readonly string[]}
        value={timing}
        onChange={setTiming}
      />
      <ChipGroup
        title="Confidence"
        options={CONFIDENCE_OPTIONS as readonly string[]}
        value={confidence}
        onChange={setConfidence}
      />

      <div>
        <div className="text-xs uppercase tracking-wide text-text-muted">Tags</div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {tags.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded-full bg-action/15 px-2 py-0.5 text-xs text-action"
            >
              {t}
              <button
                type="button"
                onClick={() => setTags(tags.filter((x) => x !== t))}
                className="text-action/70 hover:text-action"
                aria-label={`Remove ${t}`}
              >
                ×
              </button>
            </span>
          ))}
          <input
            value={tagDraft}
            onChange={(e) => setTagDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault()
                addTag(tagDraft)
              } else if (e.key === 'Backspace' && !tagDraft && tags.length > 0) {
                setTags(tags.slice(0, -1))
              }
            }}
            placeholder="add tag…"
            className="min-w-[8rem] flex-1 rounded border border-border bg-bg-card px-2 py-0.5 text-xs text-text placeholder:text-text-muted focus:border-action focus:outline-none"
          />
        </div>
        {suggestions.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <span className="text-[10px] uppercase tracking-wide text-text-muted">
              suggest:
            </span>
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => addTag(s)}
                className="rounded-full border border-border bg-bg-card px-2 py-0.5 text-[11px] text-text-muted hover:bg-bg-hover hover:text-text"
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      <div>
        <label className="text-xs uppercase tracking-wide text-text-muted">
          Why — memo for the AI
        </label>
        <textarea
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
          rows={4}
          placeholder="what were you thinking? value play, gut read, news, model…"
          className="mt-1 w-full resize-y rounded border border-border bg-bg-card px-2 py-1.5 text-xs text-text placeholder:text-text-muted focus:border-action focus:outline-none"
        />
      </div>

      <div className="flex items-center justify-end gap-2">
        {save.isError && (
          <span className="mr-auto text-xs text-loss">
            {(save.error as Error).message}
          </span>
        )}
        <button
          type="button"
          onClick={onDone}
          className="rounded border border-border px-3 py-1 text-xs text-text-muted hover:bg-bg-hover"
          disabled={save.isPending}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => save.mutate()}
          className="rounded border border-action bg-action/10 px-3 py-1 text-xs text-action hover:bg-action/20 disabled:opacity-50"
          disabled={save.isPending}
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

function ChipGroup({
  title,
  options,
  value,
  onChange,
}: {
  title: string
  options: readonly string[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-20 shrink-0 text-xs uppercase tracking-wide text-text-muted">
        {title}
      </span>
      {options.map((opt) => {
        const active = opt === value
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            className={`rounded-full border px-2.5 py-0.5 text-xs ${
              active
                ? 'border-action bg-action/15 text-action'
                : 'border-border bg-bg-card text-text-muted hover:bg-bg-hover hover:text-text'
            }`}
          >
            {opt}
          </button>
        )
      })}
    </div>
  )
}
