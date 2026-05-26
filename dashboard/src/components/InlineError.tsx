/**
 * Inline error state for a section that failed to load.
 *
 * Used in place of blanks/silent fallbacks so that when a query fails the
 * user sees a clear, scoped error in *that section only* — other sections
 * keep working. Per Phase 5 plan: 'every API failure has inline error
 * component in that section, not blank screen.'
 *
 * The message should be short and actionable. Pass the underlying error
 * (Error or string) and we'll show it small underneath.
 */
export default function InlineError({
  message,
  detail,
}: {
  message: string
  detail?: unknown
}) {
  const detailText =
    detail instanceof Error
      ? detail.message
      : typeof detail === 'string'
      ? detail
      : detail !== undefined
      ? String(detail)
      : null
  return (
    <div className="rounded-lg border border-loss bg-bg-card p-3 text-xs">
      <div className="font-semibold text-loss">⚠ {message}</div>
      {detailText && (
        <div className="mt-1 truncate font-mono text-[10px] text-text-muted" title={detailText}>
          {detailText}
        </div>
      )}
    </div>
  )
}
