'use client'

import { useMemo, useState } from 'react'
import { ArrowUpDown, Check, ChevronRight, X } from 'lucide-react'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { Modal } from '@/components/Modal'
import { DiffPanes } from '@/components/DiffPanes'
import {
  ISSUE_STATUS_LABELS,
  ISSUE_TYPE_LABELS,
  type Issue,
  type IssueSeverity,
  type IssueStatus,
} from '@/lib/types'
import { cn, relativeTime } from '@/lib/utils'

const SEVERITY_ORDER: Record<IssueSeverity, number> = { CRITICAL: 0, MAJOR: 1, MINOR: 2, INFO: 3 }

const SEVERITY_TONE: Record<IssueSeverity, 'danger' | 'warning' | 'primary' | 'neutral'> = {
  CRITICAL: 'danger',
  MAJOR: 'warning',
  MINOR: 'primary',
  INFO: 'neutral',
}

const STATUS_TONE: Record<IssueStatus, 'success' | 'warning' | 'primary' | 'danger'> = {
  AUTO_FIXED: 'success',
  APPROVED: 'primary',
  PENDING_REVIEW: 'warning',
  REJECTED: 'danger',
}

type SortKey = 'severity' | 'type' | 'confidence'

export function IssueList({
  issues,
  onDecision,
  busyId,
}: {
  issues: Issue[]
  onDecision?: (issue: Issue, status: 'APPROVED' | 'REJECTED') => void
  busyId?: string | null
}) {
  const [sortKey, setSortKey] = useState<SortKey>('severity')
  const [sortAsc, setSortAsc] = useState(true)
  const [selected, setSelected] = useState<Issue | null>(null)

  const sorted = useMemo(() => {
    const rows = [...issues]
    rows.sort((a, b) => {
      let result = 0
      if (sortKey === 'severity') result = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]
      if (sortKey === 'type') result = a.type.localeCompare(b.type)
      if (sortKey === 'confidence') result = b.confidence - a.confidence
      return sortAsc ? result : -result
    })
    return rows
  }, [issues, sortKey, sortAsc])

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc((prev) => !prev)
    else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  if (issues.length === 0) {
    return (
      <EmptyState
        icon={Check}
        title="No issues match these filters"
        description="Adjust the filters above, or this job genuinely found nothing to flag."
      />
    )
  }

  return (
    <>
      <div className="surface-panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] text-left text-sm">
            <thead className="border-b border-border bg-surface-muted/60 text-xs uppercase tracking-wide text-muted">
              <tr>
                <SortHeader label="Severity" active={sortKey === 'severity'} asc={sortAsc} onClick={() => toggleSort('severity')} />
                <SortHeader label="Type" active={sortKey === 'type'} asc={sortAsc} onClick={() => toggleSort('type')} />
                <th scope="col" className="px-4 py-3 font-medium">Description</th>
                <SortHeader label="Confidence" active={sortKey === 'confidence'} asc={sortAsc} onClick={() => toggleSort('confidence')} />
                <th scope="col" className="px-4 py-3 font-medium">Status</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sorted.map((issue) => (
                <tr key={issue._id} className="transition hover:bg-surface-muted/50">
                  <td className="whitespace-nowrap px-4 py-3">
                    <Badge tone={SEVERITY_TONE[issue.severity]} dot>
                      {issue.severity.charAt(0) + issue.severity.slice(1).toLowerCase()}
                    </Badge>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-muted">{ISSUE_TYPE_LABELS[issue.type]}</td>
                  <td className="max-w-sm px-4 py-3">
                    <button
                      onClick={() => setSelected(issue)}
                      className="group flex items-start gap-1.5 text-left text-foreground transition hover:text-primary"
                    >
                      <span className="line-clamp-2">{issue.message}</span>
                      <ChevronRight size={14} className="mt-0.5 shrink-0 opacity-0 transition group-hover:opacity-100" />
                    </button>
                    {issue.page != null && <p className="mt-0.5 text-xs text-muted">Page {issue.page}</p>}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <ConfidenceMeter value={issue.confidence} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <Badge tone={STATUS_TONE[issue.status]}>{ISSUE_STATUS_LABELS[issue.status]}</Badge>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right">
                    {onDecision && issue.status === 'PENDING_REVIEW' ? (
                      <div className="inline-flex gap-1.5">
                        <Button
                          size="sm"
                          variant="success"
                          onClick={() => onDecision(issue, 'APPROVED')}
                          loading={busyId === issue._id}
                          aria-label={`Approve: ${issue.message}`}
                        >
                          <Check size={14} /> Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => onDecision(issue, 'REJECTED')}
                          disabled={busyId === issue._id}
                          aria-label={`Reject: ${issue.message}`}
                        >
                          <X size={14} /> Reject
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-muted">{relativeTime(issue.createdAt)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <Modal
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected ? ISSUE_TYPE_LABELS[selected.type] : ''}
        description={selected?.message}
        size="lg"
        footer={
          selected && onDecision && selected.status === 'PENDING_REVIEW' ? (
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  onDecision(selected, 'REJECTED')
                  setSelected(null)
                }}
              >
                <X size={15} /> Reject
              </Button>
              <Button
                variant="success"
                onClick={() => {
                  onDecision(selected, 'APPROVED')
                  setSelected(null)
                }}
              >
                <Check size={15} /> Approve correction
              </Button>
            </>
          ) : undefined
        }
      >
        {selected && (
          <div className="space-y-5">
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Detail label="Severity" value={selected.severity} />
              <Detail label="Status" value={ISSUE_STATUS_LABELS[selected.status]} />
              <Detail label="Confidence" value={`${Math.round(selected.confidence * 100)}%`} />
              <Detail label="Page" value={selected.page != null ? String(selected.page) : '—'} />
            </dl>

            {selected.selector && (
              <div>
                <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted">Selector</p>
                <code className="block overflow-x-auto rounded-lg bg-surface-muted px-3 py-2 font-mono text-xs text-foreground">
                  {selected.selector}
                </code>
              </div>
            )}

            <DiffPanes
              before={selected.htmlText}
              after={selected.suggestion ?? selected.pdfText}
              beforeLabel="Current HTML"
              afterLabel={selected.suggestion ? 'Suggested correction' : 'Source PDF text'}
            />
          </div>
        )}
      </Modal>
    </>
  )
}

function SortHeader({
  label,
  active,
  asc,
  onClick,
}: {
  label: string
  active: boolean
  asc: boolean
  onClick: () => void
}) {
  return (
    <th scope="col" className="px-4 py-3 font-medium" aria-sort={active ? (asc ? 'ascending' : 'descending') : 'none'}>
      <button onClick={onClick} className="inline-flex items-center gap-1 transition hover:text-foreground">
        {label}
        <ArrowUpDown size={12} className={cn(active ? 'text-primary' : 'opacity-40')} />
      </button>
    </th>
  )
}

function ConfidenceMeter({ value }: { value: number }) {
  const percent = Math.round(value * 100)
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-muted">
        <div
          className={cn('h-full rounded-full', percent >= 80 ? 'bg-success-fill' : percent >= 50 ? 'bg-warning' : 'bg-danger')}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs tabular-nums text-muted">{percent}%</span>
    </div>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 text-sm text-foreground">{value}</dd>
    </div>
  )
}
