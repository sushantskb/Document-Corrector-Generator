import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export function LoadingSpinner({
  message,
  percent,
  size = 28,
  className,
}: {
  message?: string
  percent?: number
  size?: number
  className?: string
}) {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn('flex flex-col items-center justify-center gap-3 py-10 text-center', className)}
    >
      <div className="relative grid place-items-center">
        <Loader2 size={size} className="animate-spin text-primary" />
        {typeof percent === 'number' && (
          <span className="absolute text-[0.6rem] font-semibold tabular-nums text-muted">
            {Math.round(percent)}
          </span>
        )}
      </div>
      {message && <p className="text-sm text-muted">{message}</p>}
    </div>
  )
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn('h-2 w-full overflow-hidden rounded-full bg-surface-muted', className)}
    >
      <div
        className="h-full rounded-full bg-primary-fill transition-[width] duration-500 ease-out"
        style={{ width: `${clamped}%` }}
      />
    </div>
  )
}
