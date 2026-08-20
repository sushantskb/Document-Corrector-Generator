'use client'

import { useParams } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, ListChecks, Lightbulb, Printer } from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Button, ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { useJobReport } from '@/lib/hooks'
import { ISSUE_STATUS_LABELS, ISSUE_TYPE_LABELS, type IssueSeverity, type IssueType, type IssueStatus } from '@/lib/types'
import { cn, formatDate } from '@/lib/utils'

const SEVERITY_COLOR: Record<IssueSeverity, string> = {
  CRITICAL: 'bg-danger',
  MAJOR: 'bg-warning',
  MINOR: 'bg-primary-fill',
  INFO: 'bg-muted',
}

export default function ReportPage() {
  const params = useParams()
  const projectId = params.id as string
  const jobId = params.jobId as string

  const { data, loading, error, notFound } = useJobReport(jobId)

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-40 w-full rounded-2xl" />
        <Skeleton className="h-64 w-full rounded-2xl" />
      </div>
    )
  }

  if (!data) {
    return (
      <EmptyState
        icon={ListChecks}
        title={notFound ? 'Report not available' : 'Could not build the report'}
        description={notFound ? 'This job no longer exists.' : error?.message}
        action={
          <ButtonLink href={`/projects/${projectId}`} variant="secondary">
            Back to project
          </ButtonLink>
        }
      />
    )
  }

  const { job, project, summary, byType, bySeverity, byStatus, recommendations } = data
  const maxType = Math.max(1, ...Object.values(byType))

  return (
    <div className="space-y-6 print:space-y-4">
      <div className="print:hidden">
        <Link
          href={`/projects/${projectId}/jobs/${jobId}`}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-foreground"
        >
          <ArrowLeft size={16} /> Back to job
        </Link>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Verification report</h1>
          <p className="mt-1.5 text-sm text-muted">
            {project?.name ?? 'Project'} · job {jobId.slice(-8)} ·{' '}
            {formatDate(job.completedAt || job.startedAt || job.createdAt)}
          </p>
        </div>
        <div className="flex items-center gap-2 print:hidden">
          <ButtonLink href={`/projects/${projectId}/jobs/${jobId}/issues`} variant="secondary" size="sm">
            <ListChecks size={15} /> All issues
          </ButtonLink>
          <Button size="sm" onClick={() => window.print()}>
            <Printer size={15} /> Save as PDF
          </Button>
        </div>
      </div>

      {job.status !== 'COMPLETED' && (
        <Alert tone="info" title={`This job is ${job.status.toLowerCase()}`}>
          The figures below reflect what has been recorded so far.
        </Alert>
      )}

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <ScoreCard score={summary.qualityScore} />
        <SummaryCard label="Issues found" value={summary.total} />
        <SummaryCard label="Auto-fixed" value={summary.autoFixed} tone="success" />
        <SummaryCard label="Awaiting review" value={summary.pendingReview} tone="warning" />
      </section>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="surface-panel p-6 sm:p-7">
          <h2 className="mb-5 text-base font-semibold text-foreground">By severity</h2>
          {summary.total === 0 ? (
            <p className="text-sm text-muted">No issues recorded.</p>
          ) : (
            <div className="space-y-4">
              <div className="flex h-3 overflow-hidden rounded-full bg-surface-muted" role="img" aria-label="Severity distribution">
                {(Object.entries(bySeverity) as Array<[IssueSeverity, number]>).map(([severity, count]) =>
                  count > 0 ? (
                    <div
                      key={severity}
                      className={SEVERITY_COLOR[severity]}
                      style={{ width: `${(count / summary.total) * 100}%` }}
                      title={`${severity}: ${count}`}
                    />
                  ) : null
                )}
              </div>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {(Object.entries(bySeverity) as Array<[IssueSeverity, number]>).map(([severity, count]) => (
                  <div key={severity} className="flex items-center gap-2">
                    <span className={cn('h-2.5 w-2.5 shrink-0 rounded-full', SEVERITY_COLOR[severity])} />
                    <div className="min-w-0">
                      <dd className="text-sm font-semibold tabular-nums text-foreground">{count}</dd>
                      <dt className="truncate text-xs text-muted">
                        {severity.charAt(0) + severity.slice(1).toLowerCase()}
                      </dt>
                    </div>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </section>

        <section className="surface-panel p-6 sm:p-7">
          <h2 className="mb-5 text-base font-semibold text-foreground">By status</h2>
          <dl className="space-y-3">
            {(Object.entries(byStatus) as Array<[IssueStatus, number]>).map(([status, count]) => (
              <div key={status} className="flex items-center justify-between gap-3">
                <dt className="text-sm text-muted">{ISSUE_STATUS_LABELS[status]}</dt>
                <dd className="text-sm font-semibold tabular-nums text-foreground">{count}</dd>
              </div>
            ))}
            <div className="flex items-center justify-between gap-3 border-t border-border pt-3">
              <dt className="text-sm text-muted">Corrections applied</dt>
              <dd className="text-sm font-semibold tabular-nums text-foreground">{summary.correctionsApplied}</dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="surface-panel p-6 sm:p-7">
        <h2 className="mb-5 text-base font-semibold text-foreground">By issue type</h2>
        {summary.total === 0 ? (
          <p className="text-sm text-muted">No issues recorded.</p>
        ) : (
          <dl className="space-y-3">
            {(Object.entries(byType) as Array<[IssueType, number]>)
              .filter(([, count]) => count > 0)
              .sort((a, b) => b[1] - a[1])
              .map(([type, count]) => (
                <div key={type} className="flex items-center gap-4">
                  <dt className="w-36 shrink-0 truncate text-sm text-muted">{ISSUE_TYPE_LABELS[type]}</dt>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-muted">
                    <div className="h-full rounded-full bg-primary-fill" style={{ width: `${(count / maxType) * 100}%` }} />
                  </div>
                  <dd className="w-8 shrink-0 text-right text-sm font-semibold tabular-nums text-foreground">{count}</dd>
                </div>
              ))}
          </dl>
        )}
      </section>

      <section className="surface-panel p-6 sm:p-7">
        <h2 className="mb-4 flex items-center gap-2 text-base font-semibold text-foreground">
          <Lightbulb size={16} className="text-warning" /> Recommendations
        </h2>
        <ul className="space-y-2.5">
          {recommendations.map((recommendation, index) => (
            <li key={index} className="flex items-start gap-2.5 text-sm text-foreground">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
              {recommendation}
            </li>
          ))}
        </ul>
      </section>

      <section className="surface-panel p-6 sm:p-7">
        <h2 className="mb-5 text-base font-semibold text-foreground">Run details</h2>
        <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Detail label="Status" value={<Badge tone={job.status === 'COMPLETED' ? 'success' : 'neutral'}>{job.status}</Badge>} />
          <Detail label="Started" value={formatDate(job.startedAt || job.createdAt)} />
          <Detail label="Completed" value={job.completedAt ? formatDate(job.completedAt) : '—'} />
          <Detail label="Board / class" value={project ? `${project.board.replace('_', ' ')} · ${project.standard}` : '—'} />
        </dl>
      </section>
    </div>
  )
}

function ScoreCard({ score }: { score: number }) {
  const tone = score >= 90 ? 'text-success' : score >= 70 ? 'text-warning' : 'text-danger'
  return (
    <div className="surface-panel flex flex-col justify-between p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">Quality score</p>
      <p className={cn('mt-2 text-4xl font-semibold tabular-nums', tone)}>
        {score}
        <span className="text-lg text-muted">/100</span>
      </p>
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-muted">
        <div
          className={cn('h-full rounded-full', score >= 90 ? 'bg-success-fill' : score >= 70 ? 'bg-warning' : 'bg-danger')}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  tone = 'neutral',
}: {
  label: string
  value: number
  tone?: 'neutral' | 'success' | 'warning'
}) {
  return (
    <div className="surface-panel p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p
        className={cn(
          'mt-2 text-4xl font-semibold tabular-nums',
          tone === 'success' && 'text-success',
          tone === 'warning' && 'text-warning',
          tone === 'neutral' && 'text-foreground'
        )}
      >
        {value}
      </p>
    </div>
  )
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1.5 text-sm text-foreground">{value}</dd>
    </div>
  )
}
