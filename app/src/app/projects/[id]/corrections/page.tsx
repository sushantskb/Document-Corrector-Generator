'use client'

import { useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, Download, RefreshCw, RotateCcw, Undo2, Wand2 } from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Skeleton } from '@/components/ui/Skeleton'
import { DiffPanes } from '@/components/DiffPanes'
import { apiJson } from '@/lib/api'
import { useCorrections, useProjectJobs, useToast } from '@/lib/hooks'
import type { Correction, Job } from '@/lib/types'
import { downloadText } from '@/lib/download'
import { formatDate, relativeTime } from '@/lib/utils'

type Filter = 'ALL' | 'APPLIED' | 'REVERTED'

const ALL_RUNS = 'ALL_RUNS' as const

/** "20 Aug 2026 · 76 corrections" — enough to tell two runs of the same chapter apart. */
function runLabel(run: { id: string; count: number; job?: Job }) {
  const when = run.job ? formatDate(run.job.createdAt) : 'unknown date'
  return `${when} · ${run.count} correction${run.count === 1 ? '' : 's'} · ${run.id.slice(-6)}`
}

export default function CorrectionsPage() {
  const params = useParams()
  const projectId = params.id as string
  const toast = useToast()

  const { data, loading, error, refresh } = useCorrections(projectId)
  const { data: jobsData } = useProjectJobs(projectId)
  const corrections = useMemo(() => data ?? [], [data])
  const jobs = useMemo(() => jobsData ?? [], [jobsData])

  const [filter, setFilter] = useState<Filter>('ALL')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  // null means "not chosen yet" — the default is derived below, so it settles
  // correctly no matter which of the two fetches lands first.
  const [chosenRun, setChosenRun] = useState<string | typeof ALL_RUNS | null>(null)

  // A project can hold several processing runs, and each run publishes its own
  // corrected document — so the export has to name the run it belongs to.
  const runs = useMemo(() => {
    const counts = new Map<string, number>()
    for (const correction of corrections) {
      counts.set(correction.jobId, (counts.get(correction.jobId) ?? 0) + 1)
    }
    const byId = new Map(jobs.map((job) => [job._id, job]))
    return [...counts.entries()]
      .map(([id, count]) => ({ id, count, job: byId.get(id) }))
      .sort((a, b) => (b.job?.createdAt ?? '').localeCompare(a.job?.createdAt ?? ''))
  }, [corrections, jobs])

  const defaultRun =
    runs.find((run) => run.job?.correctedHtmlUrl)?.id ?? runs[0]?.id ?? ALL_RUNS
  const runId = chosenRun ?? defaultRun
  const selectedRun = runs.find((run) => run.id === runId)

  const inRun = useMemo(
    () => (runId === ALL_RUNS ? corrections : corrections.filter((c) => c.jobId === runId)),
    [corrections, runId]
  )

  const visible = useMemo(
    () => (filter === 'ALL' ? inRun : inRun.filter((c) => c.status === filter)),
    [inRun, filter]
  )

  const applied = inRun.filter((c) => c.status === 'APPLIED')

  const setStatus = async (correction: Correction, status: 'APPLIED' | 'REVERTED') => {
    setBusyId(correction._id)
    try {
      await apiJson('/api/corrections', 'PATCH', { id: correction._id, status })
      await refresh()
      toast.success(status === 'APPLIED' ? 'Correction re-applied' : 'Correction reverted')
    } catch (err) {
      toast.error('Could not update', err instanceof Error ? err.message : 'Request failed')
    } finally {
      setBusyId(null)
    }
  }

  const exportable = runId !== ALL_RUNS && Boolean(selectedRun?.job?.correctedHtmlUrl)

  const exportHtml = async () => {
    const jobId = runId
    if (!exportable || jobId === ALL_RUNS) return
    setExporting(true)
    try {
      const response = await fetch(`/api/jobs/${jobId}/corrected`)
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(body?.error || `Request failed (${response.status})`)
      }
      downloadText(`corrected-${String(jobId).slice(-8)}.html`, await response.text(), 'text/html;charset=utf-8')
      toast.success('Download started', 'The corrected document was exported.')
    } catch (err) {
      toast.error(
        'Could not export the corrected document',
        err instanceof Error ? err.message : 'Request failed'
      )
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="space-y-6">
      <Link
        href={`/projects/${projectId}`}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-foreground"
      >
        <ArrowLeft size={16} /> Back to project
      </Link>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Corrections</h1>
          <p className="mt-1.5 text-sm text-muted">
            {loading
              ? 'Loading corrections…'
              : `${inRun.length} recorded · ${applied.length} currently applied` +
                (runId === ALL_RUNS && runs.length > 1 ? ` · across ${runs.length} runs` : '')}
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          {runs.length > 0 && (
            <div className="space-y-1.5">
              <label htmlFor="run" className="block text-xs font-medium text-muted">
                Processing run
              </label>
              <select
                id="run"
                value={runId}
                onChange={(event) => setChosenRun(event.target.value)}
                className="field-input h-9 w-auto py-0 text-sm"
              >
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {runLabel(run)}
                    {run.job?.correctedHtmlUrl ? '' : ' (no document)'}
                  </option>
                ))}
                {runs.length > 1 && <option value={ALL_RUNS}>All runs</option>}
              </select>
            </div>
          )}
          <Button variant="secondary" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCw size={15} className={loading ? 'animate-spin' : undefined} /> Refresh
          </Button>
          <Button
            size="sm"
            onClick={exportHtml}
            disabled={!exportable || exporting}
            loading={exporting}
            title={
              runId === ALL_RUNS
                ? 'Choose a single run to export its corrected document'
                : exportable
                  ? undefined
                  : 'This run has not published a corrected document'
            }
          >
            <Download size={15} /> Export corrected HTML
          </Button>
        </div>
      </div>

      {error && (
        <Alert tone="error" title="Could not load corrections">
          {error.message}
        </Alert>
      )}

      {inRun.length > 0 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter corrections">
          {(['ALL', 'APPLIED', 'REVERTED'] as Filter[]).map((option) => (
            <button
              key={option}
              onClick={() => setFilter(option)}
              aria-pressed={filter === option}
              className={
                filter === option
                  ? 'rounded-lg bg-primary-fill px-3 py-1.5 text-sm font-medium text-white shadow-sm transition'
                  : 'rounded-lg border border-border bg-surface px-3 py-1.5 text-sm font-medium text-muted transition hover:border-border-strong hover:text-foreground'
              }
            >
              {option === 'ALL' ? 'All' : option.charAt(0) + option.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          <Skeleton className="h-40 w-full rounded-2xl" />
          <Skeleton className="h-40 w-full rounded-2xl" />
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Wand2}
          title={inRun.length === 0 ? 'No corrections in this run' : 'Nothing matches this filter'}
          description={
            inRun.length === 0
              ? 'Corrections appear here once a processing run applies fixes or you approve a suggestion.'
              : 'Try a different filter.'
          }
          action={
            inRun.length > 0 ? (
              <Button variant="secondary" onClick={() => setFilter('ALL')}>
                Show all
              </Button>
            ) : undefined
          }
        />
      ) : (
        <ul className="space-y-4">
          {visible.map((correction) => (
            <li key={correction._id} className="surface-panel animate-fade-up p-5 sm:p-6">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={correction.status === 'APPLIED' ? 'success' : 'neutral'} dot>
                    {correction.status === 'APPLIED' ? 'Applied' : 'Reverted'}
                  </Badge>
                  <Badge tone={correction.appliedBy === 'AUTO' ? 'primary' : 'neutral'}>
                    {correction.appliedBy === 'AUTO' ? 'Automatic' : 'Manual'}
                  </Badge>
                  {correction.selector && (
                    <code className="rounded bg-surface-muted px-2 py-0.5 font-mono text-xs text-muted">
                      {correction.selector}
                    </code>
                  )}
                  <span className="text-xs text-muted">{relativeTime(correction.createdAt)}</span>
                </div>

                {correction.status === 'APPLIED' ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setStatus(correction, 'REVERTED')}
                    loading={busyId === correction._id}
                  >
                    <Undo2 size={14} /> Revert
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setStatus(correction, 'APPLIED')}
                    loading={busyId === correction._id}
                  >
                    <RotateCcw size={14} /> Re-apply
                  </Button>
                )}
              </div>

              <DiffPanes before={correction.before} after={correction.after} beforeLabel="Before" afterLabel="After" />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
