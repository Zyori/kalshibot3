/**
 * Skeleton loading primitive. Maintains spatial layout while data loads —
 * per project CLAUDE.md ('Skeleton loaders, not spinners'), the goal is
 * that the UI doesn't jump when data arrives.
 *
 * Use one Skeleton per repeating item (row, card) rather than one giant
 * one — many small ones feel less janky and animate offset naturally.
 */
export default function Skeleton({
  className = '',
  height,
}: {
  className?: string
  height?: number | string
}) {
  return (
    <div
      className={`animate-pulse rounded-md border border-border bg-bg-card ${className}`}
      style={height !== undefined ? { height } : undefined}
    />
  )
}
