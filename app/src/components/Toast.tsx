'use client'

import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { useToast, type ToastTone } from '@/lib/hooks/useToast'
import { cn } from '@/lib/utils'

const tones: Record<ToastTone, { icon: typeof Info; accent: string }> = {
  success: { icon: CheckCircle2, accent: 'text-success' },
  error: { icon: XCircle, accent: 'text-danger' },
  warning: { icon: AlertTriangle, accent: 'text-warning' },
  info: { icon: Info, accent: 'text-primary' },
}

export function ToastViewport() {
  const { toasts, dismiss } = useToast()

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-2.5"
    >
      {toasts.map((toast) => {
        const { icon: Icon, accent } = tones[toast.tone]
        return (
          <div
            key={toast.id}
            role="status"
            className="pointer-events-auto flex animate-fade-up items-start gap-3 rounded-xl border border-border bg-surface p-4 shadow-lift"
          >
            <Icon size={18} className={cn('mt-0.5 shrink-0', accent)} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground">{toast.title}</p>
              {toast.description && <p className="mt-0.5 text-sm text-muted">{toast.description}</p>}
            </div>
            <button
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
              className="-mr-1 -mt-1 shrink-0 rounded-md p-1 text-muted transition hover:bg-surface-muted hover:text-foreground"
            >
              <X size={15} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
