'use client'

import { useEffect, useRef, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft,
  BarChart3,
  Ban,
  AlertCircle,
  Clock,
  Download,
  FileText,
  ListChecks,
  RotateCcw,
  Terminal,
} from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Badge, StatusBadge } from '@/components/ui/Badge'
import { Button, ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { ConfirmDialog } from '@/components/Modal'
import { ProgressBar } from '@/components/LoadingSpinner'
import { ProcessingSteps, estimateRemaining } from '@/components/ProcessingSteps'
import { apiJson } from '@/lib/api'
import { useIssues, useJob, useToast } from '@/lib/hooks'
import { JOB_STAGE_LABELS, type Job } from '@/lib/types'
import { cn, relativeTime } from '@/lib/utils'

export default function JobStatus() {
  const params = useParams()
  const projectId = params.id as string
  const jobId = params.jobId as string
  const toast = useToast()

  const { data: job, loading, error, notFound, refresh } = useJob(jobId)
  const issues = useIssues(job?.status === 'COMPLETED' ? jobId : undefined)

  const [confirmCancel, setConfirmCancel] = useState(false)
  const [busy, setBusy] = useState(false)
  const previousStatus = useRef<string>()

  // Notify once when a job that was running reaches a terminal state.
  useEffect(() => {
    if (!job) return
    const previous = previousStatus.current
    previousStatus.current = job.status

    if (!previous || previous === job.status) return
    if (previous !== 'PROCESSING' && previous !== 'QUEUED') return

    if (job.status === 'COMPLETED') toast.success('Processing complete', 'The verification run finished.')
    if (job.status === 'FAILED') toast.error('Processing failed', job.error || 'The job stopped with an error.')
  }, [job, toast])

  const act = async (action: 'cancel' | 'retry') => {
    setBusy(true)
    try {
      await apiJson(`/api/jobs/${jobId}`, 'PATCH', { action })
      await refresh()
      toast.success(action === 'cancel' ? 'Job cancelled' : 'Job requeued')
      setConfirmCancel(false)
    } catch (err) {
      toast.error('Action failed', err instanceof Error ? err.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-40 w-full rounded-2xl" />
        <Skeleton className="h-80 w-full rounded-2xl" />
      </div>
    )
  }

  if (!job) {
    return (
      <EmptyState
        icon={AlertCircle}
        title={notFound ? 'Job not found' : 'Could not load this job'}
        description={notFound ? 'This job may have been deleted.' : error?.message}
        action={
          <ButtonLink href={`/projects/${projectId}`} variant="secondary">
            Back to project
          </ButtonLink>
        }
      />
    )
  }

  const live = job.status === 'QUEUED' || job.status === 'PROCESSING'
  const remaining = estimateRemaining(job)
  const logs = job.logs ?? []

  return (
    <div className="space-y-6">
      <Link
        href={`/projects/${projectId}`}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-foreground"
      >
        <ArrowLeft size={16} /> Back to project
      </Link>

      {job.status === 'FAILED' && (
        <Alert tone="error" title="Processing failed">
          {job.error || 'The job stopped with an error.'}
        </Alert>
      )}

      {job.status === 'QUEUED' && (
        <Alert tone="info" title="Waiting for the processing service">
          This job is queued. It starts moving once the Python service picks it up.
        </Alert>
      )}

      <section className="surface-panel animate-fade-up p-6 sm:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">Processing job</h1>
              <StatusBadge status={job.status} />
              {live && (
                <span className="inline-flex items-center gap-1.5 text-xs text-muted">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
                  </span>
                  Live
                </span>
              )}
            </div>
            <p className="mt-1.5 font-mono text-xs text-muted">{job._id}</p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {live && (
              <Button variant="secondary" size="sm" onClick={() => setConfirmCancel(true)}>
                <Ban size={15} /> Cancel job
              </Button>
            )}
            {(job.status === 'FAILED' || job.status === 'CANCELLED') && (
              <Button size="sm" onClick={() => act('retry')} loading={busy}>
                <RotateCcw size={15} /> Retry
              </Button>
            )}
            {job.status === 'COMPLETED' && (
              <>
                {job.generatedHtmlUrl && (
                  <a
                    href={`/api/jobs/${jobId}/generated`}
                    title="Your HTML template with the PDF's missing content merged into the right sections"
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-sm font-medium text-foreground transition hover:bg-surface-hover"
                  >
                    <FileText size={15} /> Complete HTML
                  </a>
                )}
                {job.correctedHtmlUrl && (
                  <a
                    href={`/api/jobs/${jobId}/corrected`}
                    className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-sm font-medium text-foreground transition hover:bg-surface-hover"
                  >
                    <Download size={15} /> Corrected HTML
                  </a>
                )}
                <ButtonLink href={`/projects/${projectId}/jobs/${jobId}/issues`} variant="secondary" size="sm">
                  <ListChecks size={15} /> Issues
                </ButtonLink>
                <ButtonLink href={`/projects/${projectId}/jobs/${jobId}/report`} size="sm">
                  <BarChart3 size={15} /> Report
                </ButtonLink>
              </>
            )}
          </div>
        </div>

        <div className="mt-6 space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span className="font-medium text-foreground">
              {job.stage ? JOB_STAGE_LABELS[job.stage] : job.status === 'QUEUED' ? 'Queued' : 'Progress'}
            </span>
            <span className="tabular-nums text-muted">{Math.round(job.progress)}%</span>
          </div>
          <ProgressBar value={job.progress} />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
            <span className="inline-flex items-center gap-1.5">
              <Clock size={12} /> Started {relativeTime(job.startedAt || job.createdAt)}
            </span>
            {remaining && <span>{remaining} remaining</span>}
            {job.completedAt && <span>Finished {relativeTime(job.completedAt)}</span>}
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        <section className="surface-panel animate-fade-up p-6 sm:p-7 lg:col-span-2">
          <h2 className="mb-5 text-base font-semibold text-foreground">Steps</h2>
          <ProcessingSteps job={job} />
        </section>

        <div className="space-y-6 lg:col-span-3">
          <section className="surface-panel animate-fade-up p-6 sm:p-7">
            <h2 className="mb-5 text-base font-semibold text-foreground">Findings</h2>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Metric label="Issues found" value={job.stats?.issuesFound ?? 0} />
              <Metric label="Auto-fixed" value={job.stats?.autoFixed ?? 0} tone="success" />
              <Metric label="To review" value={job.stats?.pendingReview ?? 0} tone="warning" />
              <Metric
                label="Quality score"
                value={job.stats?.qualityScore != null ? `${job.stats.qualityScore}` : '—'}
              />
            </dl>

            {job.status === 'COMPLETED' && (
              <div className="mt-5 border-t border-border pt-5">
                <p className="mb-3 text-sm font-medium text-foreground">Results preview</p>
                {issues.loading ? (
                  <Skeleton className="h-16 w-full rounded-xl" />
                ) : (issues.data?.length ?? 0) === 0 ? (
                  <p className="text-sm text-muted">No issues were recorded for this run.</p>
                ) : (
                  <ul className="space-y-2">
                    {issues.data!.slice(0, 3).map((issue) => (
                      <li key={issue._id} className="flex items-start gap-2.5 rounded-xl bg-surface-muted px-3.5 py-2.5">
                        <Badge tone={issue.severity === 'CRITICAL' ? 'danger' : issue.severity === 'MAJOR' ? 'warning' : 'neutral'}>
                          {issue.severity.charAt(0) + issue.severity.slice(1).toLowerCase()}
                        </Badge>
                        <span className="min-w-0 flex-1 text-sm text-foreground line-clamp-2">{issue.message}</span>
                      </li>
                    ))}
                    {issues.data!.length > 3 && (
                      <li>
                        <Link
                          href={`/projects/${projectId}/jobs/${jobId}/issues`}
                          className="text-sm font-medium text-primary hover:underline"
                        >
                          View all {issues.data!.length} issues →
                        </Link>
                      </li>
                    )}
                  </ul>
                )}
              </div>
            )}
          </section>

          <section className="surface-panel animate-fade-up p-6 sm:p-7">
            <h2 className="mb-4 flex items-center gap-2 text-base font-semibold text-foreground">
              <Terminal size={16} className="text-muted" /> Activity
            </h2>

            {logs.length === 0 ? (
              <p className="rounded-xl border border-dashed border-border py-8 text-center text-sm text-muted">
                No activity recorded yet.
              </p>
            ) : (
              <ol className="max-h-72 space-y-2 overflow-y-auto pr-1">
                {logs
                  .slice()
                  .reverse()
                  .map((log, index) => (
                    <li key={`${log.at}-${index}`} className="flex items-start gap-3 text-sm">
                      <span
                        className={cn(
                          'mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full',
                          log.level === 'ERROR' ? 'bg-danger' : log.level === 'WARN' ? 'bg-warning' : 'bg-primary'
                        )}
                      />
                      <span className="min-w-0 flex-1 text-foreground">{log.message}</span>
                      <span className="shrink-0 text-xs tabular-nums text-muted">{relativeTime(log.at)}</span>
                    </li>
                  ))}
              </ol>
            )}
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={confirmCancel}
        onClose={() => setConfirmCancel(false)}
        onConfirm={() => act('cancel')}
        loading={busy}
        title="Cancel this job?"
        description="Processing stops where it is. You can retry the job afterwards."
        confirmLabel="Cancel job"
      />
    </div>
  )
}

function Metric({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: number | string
  tone?: 'neutral' | 'success' | 'warning'
}) {
  return (
    <div className="rounded-xl bg-surface-muted px-4 py-3">
      <dd
        className={cn(
          'text-xl font-semibold tabular-nums',
          tone === 'success' && 'text-success',
          tone === 'warning' && 'text-warning',
          tone === 'neutral' && 'text-foreground'
        )}
      >
        {value}
      </dd>
      <dt className="mt-0.5 text-xs text-muted">{label}</dt>
    </div>
  )
}
