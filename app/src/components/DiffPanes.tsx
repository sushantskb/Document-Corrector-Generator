import { cn } from '@/lib/utils'

/**
 * Side-by-side before/after text. Not a character-level diff — it shows the two
 * versions tinted so a reviewer can compare them at a glance.
 */
export function DiffPanes({
  before,
  after,
  beforeLabel = 'Current HTML',
  afterLabel = 'Suggested',
  className,
}: {
  before?: string
  after?: string
  beforeLabel?: string
  afterLabel?: string
  className?: string
}) {
  if (!before && !after) return null

  return (
    <div className={cn('grid gap-3 sm:grid-cols-2', className)}>
      <Pane label={beforeLabel} text={before} tone="before" />
      <Pane label={afterLabel} text={after} tone="after" />
    </div>
  )
}

function Pane({ label, text, tone }: { label: string; text?: string; tone: 'before' | 'after' }) {
  return (
    <div className="min-w-0">
      <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <div
        className={cn(
          'overflow-x-auto rounded-lg border px-3.5 py-3 font-mono text-xs leading-relaxed',
          tone === 'before'
            ? 'border-danger/25 bg-danger-soft/60 text-foreground'
            : 'border-success/25 bg-success-soft/60 text-foreground'
        )}
      >
        {text ? <pre className="whitespace-pre-wrap break-words">{text}</pre> : <span className="text-muted">—</span>}
      </div>
    </div>
  )
}
