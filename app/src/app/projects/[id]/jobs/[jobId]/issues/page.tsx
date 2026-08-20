'use client'

import { useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, BarChart3, Check, Download, RefreshCw, Undo2, X } from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Button, ButtonLink } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { ConfirmDialog } from '@/components/Modal'
import { IssueList } from '@/components/IssueList'
import { apiJson } from '@/lib/api'
import { useIssues, useToast } from '@/lib/hooks'
import {
  ISSUE_STATUS_LABELS,
  ISSUE_TYPE_LABELS,
  type Issue,
  type IssueSeverity,
  type IssueStatus,
  type IssueType,
} from '@/lib/types'
import { downloadCsv } from '@/lib/download'

const SEVERITIES: Array<IssueSeverity | 'ALL'> = ['ALL', 'CRITICAL', 'MAJOR', 'MINOR', 'INFO']
const STATUSES: Array<IssueStatus | 'ALL'> = ['ALL', 'PENDING_REVIEW', 'AUTO_FIXED', 'APPROVED', 'REJECTED']

export default function IssuesPage() {
  const params = useParams()
  const projectId = params.id as string
  const jobId = params.jobId as string
  const toast = useToast()

  const { data, loading, error, refresh } = useIssues(jobId)
  const issues = useMemo(() => data ?? [], [data])

  const [severity, setSeverity] = useState<IssueSeverity | 'ALL'>('ALL')
  const [status, setStatus] = useState<IssueStatus | 'ALL'>('ALL')
  const [type, setType] = useState<IssueType | 'ALL'>('ALL')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [bulk, setBulk] = useState<'APPROVED' | 'REJECTED' | 'PENDING_REVIEW' | null>(null)
  const [bulkIds, setBulkIds] = useState<string[] | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [view, setView] = useState<'list' | 'groups'>('list')

  const types = useMemo(
    () => ['ALL', ...Array.from(new Set(issues.map((issue) => issue.type)))] as Array<IssueType | 'ALL'>,
    [issues]
  )

  const filtered = useMemo(
    () =>
      issues.filter(
        (issue) =>
          (severity === 'ALL' || issue.severity === severity) &&
          (status === 'ALL' || issue.status === status) &&
          (type === 'ALL' || issue.type === type)
      ),
    [issues, severity, status, type]
  )

  const counts = useMemo(
    () => ({
      pending: issues.filter((i) => i.status === 'PENDING_REVIEW').length,
      autoFixed: issues.filter((i) => i.status === 'AUTO_FIXED').length,
      critical: issues.filter((i) => i.severity === 'CRITICAL').length,
    }),
    [issues]
  )

  const handleDecision = async (issue: Issue, next: 'APPROVED' | 'REJECTED') => {
    setBusyId(issue._id)
    try {
      await apiJson(`/api/issues/${issue._id}`, 'PATCH', { status: next })
      await refresh()
      toast.success(next === 'APPROVED' ? 'Correction approved' : 'Correction rejected')
    } catch (err) {
      toast.error('Could not update the issue', err instanceof Error ? err.message : 'Request failed')
    } finally {
      setBusyId(null)
    }
  }

  // Bulk decisions apply to the filtered list — what you see is what you decide.
  // Issues already in the target state are left out so the count is truthful.
  // Resetting only concerns issues a person actually decided on; the engine's
  // own auto-fixes are not "decisions" to undo.
  const bulkTargets = (next: 'APPROVED' | 'REJECTED' | 'PENDING_REVIEW') =>
    next === 'PENDING_REVIEW'
      ? filtered.filter((issue) => issue.status === 'APPROVED' || issue.status === 'REJECTED')
      : filtered.filter((issue) => issue.status !== next)

  const applyBulk = async () => {
    if (!bulk) return
    const targets = bulkIds
      ? filtered.filter((issue) => bulkIds.includes(issue._id) && issue.status !== bulk)
      : bulkTargets(bulk)
    setBulkBusy(true)
    try {
      const result = await apiJson<{ updated: number; rebuild: string }>('/api/issues', 'PATCH', {
        ids: targets.map((issue) => issue._id),
        status: bulk,
      })
      await refresh()
      toast.success(
        bulk === 'APPROVED'
          ? 'Corrections approved'
          : bulk === 'REJECTED'
            ? 'Corrections rejected'
            : 'Decisions cleared',
        `${result.updated} issue${result.updated === 1 ? '' : 's'} updated · the corrected document is being rebuilt.`
      )
      setBulk(null)
      setBulkIds(null)
    } catch (err) {
      toast.error('Bulk update failed', err instanceof Error ? err.message : 'Request failed')
    } finally {
      setBulkBusy(false)
    }
  }

  // 100+ pending issues are rarely 100 decisions — they are a handful of
  // *kinds* of decision repeated. Grouping by kind turns the queue into a few
  // reviewable cards, each decided at once through the same bulk endpoint.
  const groups = useMemo(() => {
    const mathy = (issue: Issue) => {
      const text = (issue.pdfText || issue.message || '').replace(/\s/g, '')
      if (text.length < 12) return false
      const letters = [...text].filter((ch) => /[a-z]/i.test(ch)).length
      return letters / text.length < 0.5
    }
    const band = (c: number) => (c >= 0.9 ? '90%+' : c >= 0.8 ? '80\u201389%' : 'below 80%')
    const hint = (issue: Issue, isMath: boolean): string => {
      if (isMath)
        return 'Equations flattened by PDF extraction \u2014 the rendered math is already in the page. Usually safe to reject.'
      if (issue.type === 'EXTRA_TEXT' && issue.severity === 'INFO')
        return "The template's own enrichment (overview bullets, tips) with no PDF counterpart. Usually safe to reject."
      if (issue.type === 'MISSING_TEXT' && issue.confidence >= 0.9)
        return 'High-confidence gaps \u2014 the PDF text has no counterpart anywhere. Usually safe to approve.'
      if (issue.type === 'ORDER_MISMATCH')
        return 'Sequence differs from the PDF \u2014 often intentional in a tabbed template. Review a sample first.'
      return 'Review the samples, then decide the whole group at once.'
    }
    const map = new Map<string, { key: string; issues: Issue[]; hint: string; label: string }>()
    for (const issue of filtered) {
      const isMath = (issue.type === 'MISSING_TEXT' || issue.type === 'TEXT_MISMATCH') && mathy(issue)
      const key = [issue.severity, issue.type, band(issue.confidence), isMath ? 'math' : 'prose'].join('|')
      if (!map.has(key)) {
        map.set(key, {
          key,
          issues: [],
          hint: hint(issue, isMath),
          label: `${issue.severity} \u00b7 ${ISSUE_TYPE_LABELS[issue.type]} \u00b7 ${band(issue.confidence)}${isMath ? ' \u00b7 flattened math' : ''}`,
        })
      }
      map.get(key)!.issues.push(issue)
    }
    return [...map.values()].sort((a, b) => b.issues.length - a.issues.length)
  }, [filtered])

  const decideGroup = (ids: string[], next: 'APPROVED' | 'REJECTED') => {
    setBulkIds(ids)
    setBulk(next)
  }

  const exportCsv = () => {
    downloadCsv(
      `issues-${jobId.slice(-8)}.csv`,
      ['Severity', 'Type', 'Status', 'Confidence', 'Page', 'Message', 'Selector'],
      filtered.map((issue) => [
        issue.severity,
        ISSUE_TYPE_LABELS[issue.type],
        ISSUE_STATUS_LABELS[issue.status],
        `${Math.round(issue.confidence * 100)}%`,
        issue.page ?? '',
        issue.message,
        issue.selector ?? '',
      ])
    )
    toast.success('Export started', 'The issues list was downloaded as CSV.')
  }

  return (
    <div className="space-y-6">
      <Link
        href={`/projects/${projectId}/jobs/${jobId}`}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-foreground"
      >
        <ArrowLeft size={16} /> Back to job
      </Link>

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Issues</h1>
          <p className="mt-1.5 text-sm text-muted">
            {loading
              ? 'Loading issues…'
              : `${issues.length} found · ${counts.autoFixed} auto-fixed · ${counts.pending} awaiting review`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" size="sm" onClick={refresh} disabled={loading}>
            <RefreshCw size={15} className={loading ? 'animate-spin' : undefined} /> Refresh
          </Button>
          <Button variant="secondary" size="sm" onClick={exportCsv} disabled={filtered.length === 0}>
            <Download size={15} /> Export CSV
          </Button>
          <ButtonLink href={`/projects/${projectId}/jobs/${jobId}/report`} size="sm">
            <BarChart3 size={15} /> Report
          </ButtonLink>
        </div>
      </div>

      {error && (
        <Alert tone="error" title="Could not load issues">
          {error.message}
        </Alert>
      )}

      {counts.critical > 0 && (
        <Alert tone="error" title={`${counts.critical} critical issue${counts.critical === 1 ? '' : 's'}`}>
          These need a manual decision before the converted HTML can be considered correct.
        </Alert>
      )}

      <div className="flex flex-col gap-3">
        <FilterRow label="Severity">
          {SEVERITIES.map((option) => (
            <Chip key={option} active={option === severity} onClick={() => setSeverity(option)}>
              {option === 'ALL' ? 'All' : option.charAt(0) + option.slice(1).toLowerCase()}
            </Chip>
          ))}
        </FilterRow>

        <FilterRow label="Status">
          {STATUSES.map((option) => (
            <Chip key={option} active={option === status} onClick={() => setStatus(option)}>
              {option === 'ALL' ? 'All' : ISSUE_STATUS_LABELS[option as IssueStatus]}
            </Chip>
          ))}
        </FilterRow>

        {types.length > 1 && (
          <FilterRow label="Type">
            {types.map((option) => (
              <Chip key={option} active={option === type} onClick={() => setType(option)}>
                {option === 'ALL' ? 'All' : ISSUE_TYPE_LABELS[option as IssueType]}
              </Chip>
            ))}
          </FilterRow>
        )}
      </div>

      {loading ? (
        <Skeleton className="h-80 w-full rounded-2xl" />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted">
                Showing {filtered.length} of {issues.length} issue{issues.length === 1 ? '' : 's'}
              </p>
              <div className="flex gap-1 rounded-lg bg-surface-muted p-1" role="group" aria-label="View">
                {(['list', 'groups'] as const).map((option) => (
                  <button
                    key={option}
                    onClick={() => setView(option)}
                    aria-pressed={view === option}
                    className={
                      view === option
                        ? 'rounded-md bg-surface px-2.5 py-1 text-xs font-medium text-foreground shadow-sm ring-1 ring-border'
                        : 'rounded-md px-2.5 py-1 text-xs font-medium text-muted hover:text-foreground'
                    }
                  >
                    {option === 'list' ? 'List' : `Groups (${groups.length})`}
                  </button>
                ))}
              </div>
            </div>
            {filtered.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setBulk('APPROVED')}
                  disabled={bulkTargets('APPROVED').length === 0 || bulkBusy}
                >
                  <Check size={15} /> Approve all ({bulkTargets('APPROVED').length})
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setBulk('REJECTED')}
                  disabled={bulkTargets('REJECTED').length === 0 || bulkBusy}
                >
                  <X size={15} /> Reject all ({bulkTargets('REJECTED').length})
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setBulk('PENDING_REVIEW')}
                  disabled={bulkTargets('PENDING_REVIEW').length === 0 || bulkBusy}
                  title="Undo approve/reject decisions and put these issues back in the queue"
                >
                  <Undo2 size={15} /> Reset to pending ({bulkTargets('PENDING_REVIEW').length})
                </Button>
              </div>
            )}
          </div>
          {view === 'list' ? (
            <IssueList issues={filtered} onDecision={handleDecision} busyId={busyId} />
          ) : (
            <ul className="space-y-4">
              {groups.map((group) => {
                const pending = group.issues.filter((i) => i.status === 'PENDING_REVIEW')
                return (
                  <li key={group.key} className="surface-panel p-5 sm:p-6">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium text-foreground">
                          {group.issues.length} × {group.label}
                        </p>
                        <p className="mt-1 text-sm text-muted">{group.hint}</p>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <Button
                          size="sm"
                          onClick={() => decideGroup(group.issues.map((i) => i._id), 'APPROVED')}
                          disabled={bulkBusy || pending.length === 0}
                        >
                          <Check size={15} /> Approve ({pending.length})
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => decideGroup(group.issues.map((i) => i._id), 'REJECTED')}
                          disabled={bulkBusy || pending.length === 0}
                        >
                          <X size={15} /> Reject ({pending.length})
                        </Button>
                      </div>
                    </div>
                    <ul className="mt-3 space-y-1.5 border-t border-border pt-3">
                      {group.issues.slice(0, 3).map((issue) => (
                        <li key={issue._id} className="truncate text-sm text-muted">
                          {issue.page ? `p${issue.page} · ` : ''}{issue.message}
                        </li>
                      ))}
                      {group.issues.length > 3 && (
                        <li className="text-xs text-muted">…and {group.issues.length - 3} more like these</li>
                      )}
                    </ul>
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}

      <ConfirmDialog
        open={bulk !== null}
        onClose={() => { setBulk(null); setBulkIds(null) }}
        onConfirm={applyBulk}
        loading={bulkBusy}
        tone={bulk === 'REJECTED' ? 'danger' : 'primary'}
        title={
          bulk === null
            ? ''
            : `${
                bulk === 'APPROVED' ? 'Approve' : bulk === 'REJECTED' ? 'Reject' : 'Reset'
              } ${(bulkIds
                ? filtered.filter((i) => bulkIds.includes(i._id) && i.status !== bulk)
                : bulkTargets(bulk)
              ).length} issue${(bulkIds
                ? filtered.filter((i) => bulkIds.includes(i._id) && i.status !== bulk)
                : bulkTargets(bulk)
              ).length === 1 ? '' : 's'}?`
        }
        description={
          bulk === 'APPROVED'
            ? 'Every issue in the current filter will be approved and its correction applied to the corrected document.'
            : bulk === 'REJECTED'
              ? 'Every issue in the current filter will be rejected, and any correction already applied for them will be undone.'
              : 'Approve and reject decisions in the current filter are cleared. Each issue returns to the verdict the processing run gave it, and the corrected document is rebuilt from that.'
        }
        confirmLabel={
          bulk === 'APPROVED' ? 'Approve all' : bulk === 'REJECTED' ? 'Reject all' : 'Reset to pending'
        }
      />
    </div>
  )
}

function FilterRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-16 shrink-0 text-xs font-medium uppercase tracking-wide text-muted">{label}</span>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={`Filter by ${label.toLowerCase()}`}>
        {children}
      </div>
    </div>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? 'rounded-lg bg-primary-fill px-2.5 py-1 text-xs font-medium text-white shadow-sm transition'
          : 'rounded-lg border border-border bg-surface px-2.5 py-1 text-xs font-medium text-muted transition hover:border-border-strong hover:text-foreground'
      }
    >
      {children}
    </button>
  )
}
