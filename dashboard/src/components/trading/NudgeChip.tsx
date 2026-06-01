import { useQuery, useQueryClient } from '@tanstack/react-query'

import type { ActiveNudge } from '../../contexts/WebSocketProvider'

/**
 * Passive amber "ask the partner?" chips. Fed push-only by the WS `nudge`
 * event into the ['nudges'] client cache (no GET — they're ephemeral). A chip
 * is a reminder to open a terminal session, never advice and never an action.
 * Dismissing just drops it from the local cache.
 */
export default function NudgeChips() {
  const queryClient = useQueryClient()
  const { data } = useQuery<ActiveNudge[]>({
    queryKey: ['nudges'],
    queryFn: () => queryClient.getQueryData<ActiveNudge[]>(['nudges']) ?? [],
    staleTime: Infinity,
  })
  const nudges = data ?? []
  if (nudges.length === 0) return null

  const drop = (subject: string, trigger: string) => {
    queryClient.setQueryData<ActiveNudge[]>(['nudges'], (prev) =>
      (prev ?? []).filter((n) => !(n.subject === subject && n.trigger === trigger)),
    )
  }

  return (
    <div className="flex flex-wrap gap-2">
      {nudges.map((n) => (
        <span
          key={`${n.subject}:${n.trigger}`}
          className="inline-flex items-center gap-2 rounded-full border border-action/40 bg-action/10 px-3 py-1 text-[11px] text-action"
        >
          {n.label}
          <button
            type="button"
            onClick={() => drop(n.subject, n.trigger)}
            className="text-action/60 hover:text-action"
            aria-label="dismiss nudge"
          >
            ×
          </button>
        </span>
      ))}
    </div>
  )
}
