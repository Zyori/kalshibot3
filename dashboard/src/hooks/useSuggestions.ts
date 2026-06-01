import { useQuery } from '@tanstack/react-query'

import type { Suggestion } from '../lib/types'

type SuggestionsResponse = { suggestions: Suggestion[] }

/**
 * Pending AI-partner suggestions. Cold-loaded from /api/partner/suggestions;
 * the WS `suggestion` event invalidates ['suggestions'] so a staged or
 * dismissed card refetches immediately (discrete-event pattern, like
 * position_synced — never setQueryData of hot data here).
 *
 * A slow backstop poll covers a dropped WS message.
 */
export function useSuggestions() {
  const { data, isError, error } = useQuery<SuggestionsResponse>({
    queryKey: ['suggestions'],
    queryFn: async () => {
      const res = await fetch('/api/partner/suggestions?status=pending')
      if (!res.ok) throw new Error(`/api/partner/suggestions: ${res.status}`)
      return res.json()
    },
    refetchInterval: 30_000,
  })
  return { suggestions: data?.suggestions ?? [], isError, error }
}
