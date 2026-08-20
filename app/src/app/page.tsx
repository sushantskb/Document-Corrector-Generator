'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  FileText,
  FolderOpen,
  ListChecks,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Badge, StatusBadge } from '@/components/ui/Badge'
import { Button, ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { CardSkeleton } from '@/components/ui/Skeleton'
import { useProjects } from '@/lib/hooks'
import type { Project } from '@/lib/types'
import { relativeTime } from '@/lib/utils'

const PAGE_SIZE = 9

export default function Dashboard() {
  const { data, loading, error, refresh } = useProjects()
  const projects = useMemo(() => data ?? [], [data])

  const [query, setQuery] = useState('')
  const [board, setBoard] = useState('ALL')
  const [status, setStatus] = useState<'ALL' | 'ACTIVE' | 'ARCHIVED'>('ALL')
  const [page, setPage] = useState(1)

  const boards = useMemo(() => ['ALL', ...Array.from(new Set(projects.map((p) => p.board)))], [projects])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return projects.filter((project) => {
      const matchesBoard = board === 'ALL' || project.board === board
      const matchesStatus = status === 'ALL' || project.status === status
      const matchesQuery =
        !q ||
        project.name.toLowerCase().includes(q) ||
        project.subject.toLowerCase().includes(q) ||
        String(project.standard).includes(q)
      return matchesBoard && matchesStatus && matchesQuery
    })
  }, [projects, query, board, status])

  // Any filter change invalidates the current page number.
  useEffect(() => {
    setPage(1)
  }, [query, board, status])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  const totals = useMemo(
    () =>
      projects.reduce(
        (acc, project) => ({
          active: acc.active + (project.status === 'ACTIVE' ? 1 : 0),
          documents: acc.documents + (project.stats?.documents ?? 0),
          completedJobs: acc.completedJobs + (project.stats?.completedJobs ?? 0),
          pendingIssues: acc.pendingIssues + (project.stats?.pendingIssues ?? 0),
        }),
        { active: 0, documents: 0, completedJobs: 0, pendingIssues: 0 }
      ),
    [projects]
  )

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">Projects</h1>
          <p className="mt-1.5 text-sm text-muted">
            {loading ? 'Loading your workspace…' : `${projects.length} project${projects.length === 1 ? '' : 's'} · ${totals.active} active`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? 'animate-spin' : undefined} />
            Refresh
          </Button>
          <ButtonLink href="/projects/new">
            <Plus size={17} /> New Project
          </ButtonLink>
        </div>
      </div>

      {error && (
        <Alert tone="error" title="Unable to reach the API">
          {error.message} — check that MongoDB is running and MONGODB_URI is set.
        </Alert>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile label="Projects" value={projects.length} icon={FolderOpen} />
          <StatTile label="Documents" value={totals.documents} icon={FileText} />
          <StatTile label="Jobs completed" value={totals.completedJobs} icon={ListChecks} />
          <StatTile label="Issues to review" value={totals.pendingIssues} icon={ListChecks} tone={totals.pendingIssues > 0 ? 'warning' : 'neutral'} />
        </div>
      )}

      {projects.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative min-w-[16rem] flex-1">
            <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name, subject, or standard…"
              aria-label="Search projects"
              className="field-input pl-10"
            />
          </div>

          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by board">
            {boards.map((option) => (
              <FilterChip key={option} active={option === board} onClick={() => setBoard(option)}>
                {option === 'ALL' ? 'All boards' : option.replace('_', ' ')}
              </FilterChip>
            ))}
          </div>

          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
            {(['ALL', 'ACTIVE', 'ARCHIVED'] as const).map((option) => (
              <FilterChip key={option} active={option === status} onClick={() => setStatus(option)}>
                {option === 'ALL' ? 'Any status' : option.charAt(0) + option.slice(1).toLowerCase()}
              </FilterChip>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      ) : projects.length === 0 ? (
        <EmptyState
          icon={FolderOpen}
          title="No projects yet"
          description="Create a project to upload a PDF and its converted HTML, then run a verification pass."
          action={
            <ButtonLink href="/projects/new" size="lg">
              <Plus size={17} /> Create your first project
            </ButtonLink>
          }
        />
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Search}
          title="No matching projects"
          description="Try a different search term, or clear the filters."
          action={
            <Button
              variant="secondary"
              onClick={() => {
                setQuery('')
                setBoard('ALL')
                setStatus('ALL')
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
            {visible.map((project, index) => (
              <ProjectCard key={project._id} project={project} index={index} />
            ))}
          </div>

          {pageCount > 1 && (
            <nav aria-label="Pagination" className="flex items-center justify-between gap-3">
              <p className="text-sm text-muted">
                Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} of{' '}
                {filtered.length}
              </p>
              <div className="flex items-center gap-1.5">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage === 1}
                  aria-label="Previous page"
                >
                  <ChevronLeft size={15} /> Previous
                </Button>
                <span className="px-2 text-sm tabular-nums text-muted">
                  {currentPage} / {pageCount}
                </span>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                  disabled={currentPage === pageCount}
                  aria-label="Next page"
                >
                  Next <ChevronRight size={15} />
                </Button>
              </div>
            </nav>
          )}
        </>
      )}
    </div>
  )
}

function ProjectCard({ project, index }: { project: Project; index: number }) {
  const stats = project.stats

  return (
    <div
      style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
      className="surface-panel group flex animate-fade-up flex-col p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lift"
    >
      <div className="flex items-start justify-between gap-3">
        <Link href={`/projects/${project._id}`} className="min-w-0 flex-1">
          <h3 className="truncate font-semibold text-foreground transition group-hover:text-primary">{project.name}</h3>
        </Link>
        <StatusBadge status={project.status} className="shrink-0" />
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5">
        <Badge tone="primary">{project.board.replace('_', ' ')}</Badge>
        <Badge>Class {project.standard}</Badge>
        <Badge>{project.subject}</Badge>
      </div>

      {stats && (
        <dl className="mt-4 grid grid-cols-3 gap-2 rounded-xl bg-surface-muted px-3 py-2.5 text-center">
          <MiniStat label="Files" value={stats.documents} />
          <MiniStat label="Jobs" value={stats.completedJobs} />
          <MiniStat label="To review" value={stats.pendingIssues} highlight={stats.pendingIssues > 0} />
        </dl>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 border-t border-border pt-3.5">
        <span className="text-xs text-muted">Created {relativeTime(project.createdAt)}</span>
        <div className="flex items-center gap-1">
          <Link
            href={`/projects/${project._id}/corrections`}
            className="rounded-lg px-2 py-1 text-xs font-medium text-muted transition hover:bg-surface-muted hover:text-foreground"
          >
            Corrections
          </Link>
          <Link
            href={`/projects/${project._id}`}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-primary transition hover:bg-primary-soft"
          >
            Open <ArrowRight size={13} />
          </Link>
        </div>
      </div>
    </div>
  )
}

function MiniStat({ label, value, highlight }: { label: string; value: number; highlight?: boolean }) {
  return (
    <div>
      <dt className="sr-only">{label}</dt>
      <dd className={highlight ? 'text-sm font-semibold tabular-nums text-warning' : 'text-sm font-semibold tabular-nums text-foreground'}>
        {value}
      </dd>
      <p className="text-[0.6875rem] text-muted">{label}</p>
    </div>
  )
}

function StatTile({
  label,
  value,
  icon: Icon,
  tone = 'neutral',
}: {
  label: string
  value: number
  icon: LucideIcon
  tone?: 'neutral' | 'warning'
}) {
  return (
    <div className="surface-panel flex items-center gap-3 p-4">
      <span
        className={
          tone === 'warning'
            ? 'grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-warning-soft text-warning'
            : 'grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary'
        }
      >
        <Icon size={17} />
      </span>
      <div className="min-w-0">
        <p className="text-xl font-semibold tabular-nums leading-tight text-foreground">{value}</p>
        <p className="truncate text-xs text-muted">{label}</p>
      </div>
    </div>
  )
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? 'rounded-lg bg-primary-fill px-3 py-1.5 text-sm font-medium text-white shadow-sm transition'
          : 'rounded-lg border border-border bg-surface px-3 py-1.5 text-sm font-medium text-muted transition hover:border-border-strong hover:text-foreground'
      }
    >
      {children}
    </button>
  )
}
