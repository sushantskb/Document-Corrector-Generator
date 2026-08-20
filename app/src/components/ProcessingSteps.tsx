'use client'

import { Check, Loader2, X } from 'lucide-react'
import { JOB_STAGES, JOB_STAGE_LABELS, type Job } from '@/lib/types'
import { cn } from '@/lib/utils'

type StepState = 'done' | 'active' | 'pending' | 'failed'

function stepStates(job: Job): StepState[] {
  const currentIndex = job.stage ? JOB_STAGES.indexOf(job.stage) : -1

  return JOB_STAGES.map((_, index) => {
    if (job.status === 'COMPLETED') return 'done'
    if (job.status === 'FAILED' || job.status === 'CANCELLED') {
      if (currentIndex === -1) return 'pending'
      if (index < currentIndex) return 'done'
      return index === currentIndex ? 'failed' : 'pending'
    }
    if (currentIndex === -1) return 'pending'
    if (index < currentIndex) return 'done'
    return index === currentIndex ? 'active' : 'pending'
  })
}

export function ProcessingSteps({ job }: { job: Job }) {
  const states = stepStates(job)

  return (
    <ol className="space-y-0" aria-label="Processing steps">
      {JOB_STAGES.map((stage, index) => {
        const state = states[index]
        const last = index === JOB_STAGES.length - 1

        return (
          <li key={stage} className="flex gap-3.5">
            <div className="flex flex-col items-center">
              <span
                aria-hidden="true"
                className={cn(
                  'grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold transition-colors',
                  state === 'done' && 'bg-success-fill text-white',
                  state === 'active' && 'bg-primary-fill text-white',
                  state === 'failed' && 'bg-danger text-white',
                  state === 'pending' && 'border border-border-strong bg-surface text-muted'
                )}
              >
                {state === 'done' && <Check size={14} strokeWidth={3} />}
                {state === 'active' && <Loader2 size={13} className="animate-spin" />}
                {state === 'failed' && <X size={14} strokeWidth={3} />}
                {state === 'pending' && index + 1}
              </span>
              {!last && (
                <span
                  aria-hidden="true"
                  className={cn(
                    'w-px flex-1 transition-colors',
                    state === 'done' ? 'bg-success-fill' : 'bg-border'
                  )}
                />
              )}
            </div>

            <div className={cn('min-w-0 flex-1', last ? 'pb-0' : 'pb-6')}>
              <p
                className={cn(
                  'text-sm font-medium',
                  state === 'pending' ? 'text-muted' : 'text-foreground'
                )}
              >
                {JOB_STAGE_LABELS[stage]}
              </p>
              {state === 'active' && (
                <p className="mt-0.5 text-xs text-muted">In progress · {Math.round(job.progress)}%</p>
              )}
              {state === 'failed' && (
                <p className="mt-0.5 text-xs text-danger">
                  {job.status === 'CANCELLED' ? 'Cancelled at this step' : 'Failed at this step'}
                </p>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

/**
 * Rough remaining-time estimate from elapsed time and percent complete.
 * Returns null until there is enough progress for the number to mean anything.
 */
export function estimateRemaining(job: Job): string | null {
  if (job.status !== 'PROCESSING' || !job.startedAt || job.progress < 5) return null

  const elapsedMs = Date.now() - new Date(job.startedAt).getTime()
  const totalMs = elapsedMs / (job.progress / 100)
  const remainingMs = totalMs - elapsedMs
  if (!Number.isFinite(remainingMs) || remainingMs <= 0) return null

  const minutes = Math.round(remainingMs / 60000)
  if (minutes < 1) return 'less than a minute'
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? '' : 's'}`

  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (hours >= 24) return 'over a day'
  return rest === 0
    ? `about ${hours} hour${hours === 1 ? '' : 's'}`
    : `about ${hours}h ${rest}m`
}
