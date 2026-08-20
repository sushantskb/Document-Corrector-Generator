'use client'

import { useCallback, useMemo, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft,
  Check,
  Eye,
  FileCode2,
  FileText,
  Play,
  Trash2,
  UploadCloud,
  Wand2,
} from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Badge, StatusBadge } from '@/components/ui/Badge'
import { Button, ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { FileDrop } from '@/components/ui/FileDrop'
import { Skeleton } from '@/components/ui/Skeleton'
import { ConfirmDialog, Modal } from '@/components/Modal'
import { DocumentPreview } from '@/components/DocumentPreview'
import { ProgressBar } from '@/components/LoadingSpinner'
import { api, apiJson } from '@/lib/api'
import { useDocuments, useProject, useProjectJobs, useToast } from '@/lib/hooks'
import type { DocumentRecord, Job } from '@/lib/types'
import { formatBytes, relativeTime } from '@/lib/utils'

export default function ProjectDetail() {
  const params = useParams()
  const projectId = params.id as string
  const router = useRouter()
  const toast = useToast()

  const project = useProject(projectId)
  const documents = useDocuments(projectId)
  const jobs = useProjectJobs(projectId)

  const [pdfFile, setPdfFile] = useState<File | null>(null)
  const [htmlFile, setHtmlFile] = useState<File | null>(null)
  const [uploadPercent, setUploadPercent] = useState<number | null>(null)
  const [starting, setStarting] = useState(false)
  const [previewDoc, setPreviewDoc] = useState<DocumentRecord | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<DocumentRecord | null>(null)
  const [deleting, setDeleting] = useState(false)

  const docs = useMemo(() => documents.data ?? [], [documents.data])
  const jobList = useMemo(() => jobs.data ?? [], [jobs.data])
  const pdfs = docs.filter((d) => d.type === 'PDF')
  const htmls = docs.filter((d) => d.type === 'HTML')
  const readyToProcess = pdfs.length > 0 && htmls.length > 0

  const uploadOne = useCallback(async (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('folder', projectId)

    const res = await fetch('/api/upload', { method: 'POST', body: formData })
    const data = await res.json()
    if (!res.ok) throw new Error(data?.error || 'Upload failed')
    return data
  }, [projectId])

  const handleUpload = async () => {
    if (!pdfFile || !htmlFile) return
    setUploadPercent(5)

    try {
      const pdfData = await uploadOne(pdfFile)
      setUploadPercent(40)
      const htmlData = await uploadOne(htmlFile)
      setUploadPercent(70)

      await apiJson('/api/documents', 'POST', {
        projectId,
        type: 'PDF',
        originalName: pdfFile.name,
        ...pdfData,
      })
      setUploadPercent(85)

      await apiJson('/api/documents', 'POST', {
        projectId,
        type: 'HTML',
        originalName: htmlFile.name,
        ...htmlData,
      })
      setUploadPercent(100)

      setPdfFile(null)
      setHtmlFile(null)
      await documents.refresh()
      toast.success('Files uploaded', 'Both documents are stored and ready to process.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Upload failed'
      toast.error('Upload failed', message)
    } finally {
      setTimeout(() => setUploadPercent(null), 400)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)

    try {
      await api(`/api/documents/${deleteTarget._id}`, { method: 'DELETE' })
      await documents.refresh()
      toast.success('Document deleted', `The ${deleteTarget.type} was removed from this project.`)
      setDeleteTarget(null)
    } catch (error) {
      toast.error('Could not delete', error instanceof Error ? error.message : 'Request failed')
    } finally {
      setDeleting(false)
    }
  }

  const handleStartProcessing = async () => {
    if (!readyToProcess) return
    setStarting(true)

    try {
      const job = await apiJson<Job>('/api/jobs', 'POST', {
        projectId,
        pdfDocumentId: pdfs[0]._id,
        htmlDocumentId: htmls[0]._id,
      })
      toast.success('Processing started', 'Tracking progress on the job page.')
      router.push(`/projects/${projectId}/jobs/${job._id}`)
    } catch (error) {
      toast.error('Could not start processing', error instanceof Error ? error.message : 'Request failed')
      setStarting(false)
    }
  }

  if (project.loading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-32 w-full rounded-2xl" />
        <Skeleton className="h-72 w-full rounded-2xl" />
      </div>
    )
  }

  if (!project.data) {
    return (
      <EmptyState
        icon={FileText}
        title={project.notFound ? 'Project not found' : 'Could not load this project'}
        description={
          project.notFound
            ? 'This project may have been deleted.'
            : project.error?.message || 'The database may be unreachable.'
        }
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => router.push('/')}>
              Back to projects
            </Button>
            <Button onClick={project.refresh}>Try again</Button>
          </div>
        }
      />
    )
  }

  const data = project.data

  return (
    <div className="space-y-6">
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-foreground"
      >
        <ArrowLeft size={16} /> Back to projects
      </Link>

      <section className="surface-panel animate-fade-up p-6 sm:p-7">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="truncate text-2xl font-semibold tracking-tight text-foreground">{data.name}</h1>
              <StatusBadge status={data.status} />
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <Badge tone="primary">{data.board.replace('_', ' ')}</Badge>
              <Badge>Class {data.standard}</Badge>
              <Badge>{data.subject}</Badge>
              {data.language && <Badge>{data.language}</Badge>}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <ButtonLink href={`/projects/${projectId}/corrections`} variant="secondary" size="sm">
              <Wand2 size={15} /> Corrections
            </ButtonLink>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPreviewDoc(pdfs[0] ?? htmls[0] ?? null)}
              disabled={docs.length === 0}
            >
              <Eye size={15} /> Compare
            </Button>
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        <section className="surface-panel animate-fade-up p-6 sm:p-7 lg:col-span-3">
          <div className="mb-5">
            <h2 className="text-base font-semibold text-foreground">Upload source files</h2>
            <p className="mt-1 text-sm text-muted">
              Add the original PDF and its converted HTML. Both are required to start a verification run.
            </p>
          </div>

          <div className="space-y-3">
            <FileDrop label="PDF" accept=".pdf" hint="The original textbook PDF" file={pdfFile} onSelect={setPdfFile} icon={FileText} />
            <FileDrop
              label="HTML"
              accept=".html,.htm"
              hint="The converted HTML output"
              file={htmlFile}
              onSelect={setHtmlFile}
              icon={FileCode2}
            />
          </div>

          {uploadPercent !== null && (
            <div className="mt-5 space-y-2">
              <div className="flex items-center justify-between text-xs text-muted">
                <span>Uploading…</span>
                <span className="tabular-nums">{uploadPercent}%</span>
              </div>
              <ProgressBar value={uploadPercent} />
            </div>
          )}

          <Button
            onClick={handleUpload}
            loading={uploadPercent !== null}
            disabled={!pdfFile || !htmlFile}
            size="lg"
            className="mt-5 w-full"
          >
            {uploadPercent === null && <UploadCloud size={17} />}
            {uploadPercent !== null ? 'Uploading…' : 'Upload both files'}
          </Button>

          {(!pdfFile || !htmlFile) && uploadPercent === null && (
            <p className="mt-2.5 text-center text-xs text-muted">Select one PDF and one HTML file to continue.</p>
          )}
        </section>

        <section className="surface-panel animate-fade-up flex flex-col p-6 sm:p-7 lg:col-span-2">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-base font-semibold text-foreground">Documents</h2>
            {docs.length > 0 && <Badge>{docs.length}</Badge>}
          </div>

          {documents.loading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full rounded-xl" />
              <Skeleton className="h-16 w-full rounded-xl" />
            </div>
          ) : docs.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center rounded-xl border border-dashed border-border py-12 text-center">
              <FileText size={22} className="mb-2.5 text-muted" />
              <p className="text-sm font-medium text-foreground">Nothing uploaded yet</p>
              <p className="mt-1 text-xs text-muted">Uploaded files appear here.</p>
            </div>
          ) : (
            <ul className="flex-1 space-y-2">
              {docs.map((doc) => (
                <li
                  key={doc._id}
                  className="flex items-center gap-3 rounded-xl border border-border bg-surface-muted/60 px-3.5 py-3 transition hover:border-border-strong"
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-surface text-primary shadow-sm">
                    {doc.type === 'PDF' ? <FileText size={16} /> : <FileCode2 size={16} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-foreground">{doc.originalName || doc.type}</p>
                      {doc.version && doc.version > 1 && <Badge>v{doc.version}</Badge>}
                    </div>
                    <p className="truncate text-xs text-muted">
                      {doc.type} · {formatBytes(doc.size)} · {relativeTime(doc.createdAt)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <StatusBadge status={doc.status} />
                    <button
                      onClick={() => setPreviewDoc(doc)}
                      aria-label={`Preview ${doc.type}`}
                      className="rounded-lg p-1.5 text-muted transition hover:bg-surface hover:text-foreground"
                    >
                      <Eye size={15} />
                    </button>
                    <button
                      onClick={() => setDeleteTarget(doc)}
                      aria-label={`Delete ${doc.type}`}
                      className="rounded-lg p-1.5 text-muted transition hover:bg-surface hover:text-danger"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-6 border-t border-border pt-5">
            <div className="mb-3 space-y-1.5">
              <Requirement met={pdfs.length > 0} label="PDF uploaded" />
              <Requirement met={htmls.length > 0} label="HTML uploaded" />
            </div>
            <Button
              onClick={handleStartProcessing}
              disabled={!readyToProcess}
              loading={starting}
              variant="success"
              size="lg"
              className="w-full"
            >
              {!starting && <Play size={16} />}
              {starting ? 'Starting…' : 'Start processing'}
            </Button>
          </div>
        </section>
      </div>

      <section className="surface-panel animate-fade-up p-6 sm:p-7">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground">Job history</h2>
          {jobList.length > 0 && <Badge>{jobList.length}</Badge>}
        </div>

        {jobs.loading ? (
          <Skeleton className="h-24 w-full rounded-xl" />
        ) : jobList.length === 0 ? (
          <p className="rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted">
            No processing runs yet for this project.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {jobList.map((job) => (
              <li key={job._id}>
                <Link
                  href={`/projects/${projectId}/jobs/${job._id}`}
                  className="flex flex-wrap items-center gap-3 py-3 transition hover:opacity-80"
                >
                  <StatusBadge status={job.status} />
                  <span className="font-mono text-xs text-muted">{job._id.slice(-8)}</span>
                  <span className="text-sm text-muted">Started {relativeTime(job.startedAt || job.createdAt)}</span>
                  {job.stats && job.stats.issuesFound > 0 && (
                    <Badge tone="warning">{job.stats.issuesFound} issues</Badge>
                  )}
                  <span className="ml-auto text-xs font-medium text-primary">View</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Modal
        open={Boolean(previewDoc)}
        onClose={() => setPreviewDoc(null)}
        title="Document preview"
        description="Source PDF and converted HTML, side by side."
        size="xl"
      >
        <div className="h-[60vh]">
          <DocumentPreview documents={docs} compare />
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        loading={deleting}
        title={`Delete this ${deleteTarget?.type ?? 'document'}?`}
        description="The file is removed from storage and from this project. This cannot be undone."
        confirmLabel="Delete"
      />
    </div>
  )
}

function Requirement({ met, label }: { met: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span
        className={
          met
            ? 'grid h-4 w-4 place-items-center rounded-full bg-success-fill text-white'
            : 'grid h-4 w-4 place-items-center rounded-full border border-border-strong'
        }
      >
        {met && <Check size={11} strokeWidth={3} />}
      </span>
      <span className={met ? 'text-muted line-through' : 'text-muted'}>{label}</span>
    </div>
  )
}
