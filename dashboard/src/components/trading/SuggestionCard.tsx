import { useMutation, useQueryClient } from '@tanstack/react-query'

import { formatDollars, formatPriceCents } from '../../lib/format'
import type { Suggestion } from '../../lib/types'

/**
 * An amber card for one AI-partner suggestion. Entry cards carry "Stage This
 * Bet"; exit cards carry "Stage Sell". The Stage button does NOT place an
 * order — it pre-fills the OrderPanel via `onStage`, where the user reviews
 * and confirms. Amber only (never green/red): a suggestion is proposed
 * action, not realized money.
 */
export default function SuggestionCard({
  suggestion,
  onStage,
}: {
  suggestion: Suggestion
  onStage: (s: Suggestion) => void
}) {
  const queryClient = useQueryClient()

  const dismiss = useMutation({
    mutationFn: async () => {
      const res = await fetch(
        `/api/partner/suggestions/${suggestion.id}/dismiss`,
        { method: 'POST' },
      )
      if (!res.ok) throw new Error(`dismiss: ${res.status}`)
      return res.json()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions'] })
    },
  })

  const isExit = suggestion.kind === 'exit'
  const stageLabel = isExit ? 'Stage Sell' : 'Stage This Bet'
  const sizeUnits = suggestion.suggested_size_cents

  return (
    <div className="rounded-lg border border-action/40 bg-action/5 p-3 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <span className="rounded-full bg-action/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-action">
            {isExit ? 'Exit' : 'Entry'}
          </span>
          <span className="font-mono tabular-nums text-text">
            {suggestion.side.toUpperCase()} @ {formatPriceCents(suggestion.suggested_price_cents)}
          </span>
          <span className="text-text-muted">·</span>
          <span className="text-text-muted">{suggestion.strategy}</span>
          <span className="text-text-muted">·</span>
          <span className="text-text-muted">{suggestion.confidence}</span>
        </span>
        <span className="font-mono tabular-nums text-text-muted">
          {formatDollars(sizeUnits)}
        </span>
      </div>

      <p className="mb-3 text-text-muted">{suggestion.justification}</p>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onStage(suggestion)}
          className="rounded-md bg-action px-3 py-1.5 text-[11px] font-semibold text-bg hover:bg-action/90"
        >
          {stageLabel}
        </button>
        <button
          type="button"
          onClick={() => dismiss.mutate()}
          disabled={dismiss.isPending}
          className="rounded-md border border-border px-3 py-1.5 text-[11px] text-text-muted hover:bg-bg-hover disabled:opacity-50"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
