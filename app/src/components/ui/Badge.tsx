import { cn } from '@/lib/utils'

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const tones: Record<Tone, string> = {
  neutral: 'bg-surface-muted text-muted ring-border',
  primary: 'bg-primary-soft text-primary ring-primary/20',
  success: 'bg-success-soft text-success ring-success/20',
  warning: 'bg-warning-soft text-warning ring-warning/20',
  danger: 'bg-danger-soft text-danger ring-danger/20',
}

export const statusTone: Record<string, Tone> = {
  ACTIVE: 'success',
  ARCHIVED: 'neutral',
  QUEUED: 'neutral',
  UPLOADED: 'primary',
  PROCESSING: 'warning',
  READY: 'success',
  COMPLETED: 'success',
  FAILED: 'danger',
}

export function Badge({
  tone = 'neutral',
  dot,
  className,
  children,
}: {
  tone?: Tone
  dot?: boolean
  className?: string
  children: React.ReactNode
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        tones[tone],
        className
      )}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

export function StatusBadge({ status, className }: { status?: string; className?: string }) {
  if (!status) return null
  return (
    <Badge tone={statusTone[status] ?? 'neutral'} dot className={className}>
      {status.charAt(0) + status.slice(1).toLowerCase().replace('_', ' ')}
    </Badge>
  )
}
