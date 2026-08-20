'use client'

import { AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

type Tone = 'info' | 'success' | 'error'

const tones: Record<Tone, { wrap: string; icon: typeof Info }> = {
  info: { wrap: 'border-primary/25 bg-primary-soft text-primary', icon: Info },
  success: { wrap: 'border-success/25 bg-success-soft text-success', icon: CheckCircle2 },
  error: { wrap: 'border-danger/25 bg-danger-soft text-danger', icon: AlertTriangle },
}

export function Alert({
  tone = 'info',
  title,
  children,
  onDismiss,
  className,
}: {
  tone?: Tone
  title?: string
  children?: React.ReactNode
  onDismiss?: () => void
  className?: string
}) {
  const { wrap, icon: Icon } = tones[tone]
  return (
    <div
      role="status"
      className={cn('flex animate-fade-up items-start gap-3 rounded-xl border px-4 py-3 text-sm', wrap, className)}
    >
      <Icon size={18} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={cn('text-foreground/80', title && 'mt-0.5')}>{children}</div>}
      </div>
      {onDismiss && (
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          className="-mr-1 shrink-0 rounded-md p-1 opacity-60 transition hover:bg-foreground/5 hover:opacity-100"
        >
          <X size={15} />
        </button>
      )}
    </div>
  )
}
