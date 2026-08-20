'use client'

import { useState } from 'react'
import { ExternalLink, FileCode2, FileText, ZoomIn, ZoomOut } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import type { DocumentRecord } from '@/lib/types'
import { cn, formatBytes } from '@/lib/utils'

const ZOOM_STEPS = [50, 75, 100, 125, 150, 200]

function Frame({ doc, zoom }: { doc: DocumentRecord; zoom: number }) {
  return (
    <div className="h-full overflow-auto rounded-xl border border-border bg-white">
      <iframe
        src={doc.cloudinaryUrl}
        title={`${doc.type} preview`}
        sandbox=""
        className="border-0 bg-white"
        style={{
          width: `${(100 / zoom) * 100}%`,
          height: `${(100 / zoom) * 100}%`,
          transform: `scale(${zoom / 100})`,
          transformOrigin: 'top left',
        }}
      />
    </div>
  )
}

export function DocumentPreview({
  documents,
  compare = false,
  className,
}: {
  documents: DocumentRecord[]
  compare?: boolean
  className?: string
}) {
  const [zoom, setZoom] = useState(100)
  const pdf = documents.find((d) => d.type === 'PDF')
  const html = documents.find((d) => d.type === 'HTML')
  const shown = compare ? [pdf, html].filter(Boolean) as DocumentRecord[] : documents.slice(0, 1)

  if (shown.length === 0) {
    return (
      <div className="grid place-items-center rounded-xl border border-dashed border-border py-16 text-center">
        <FileText size={22} className="mb-2 text-muted" />
        <p className="text-sm text-muted">Nothing to preview yet.</p>
      </div>
    )
  }

  const stepZoom = (direction: 1 | -1) => {
    const index = ZOOM_STEPS.indexOf(zoom)
    const next = ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, Math.max(0, index + direction))]
    setZoom(next)
  }

  return (
    <div className={cn('flex h-full min-h-0 flex-col gap-3', className)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => stepZoom(-1)}
            disabled={zoom === ZOOM_STEPS[0]}
            aria-label="Zoom out"
          >
            <ZoomOut size={15} />
          </Button>
          <span className="w-12 text-center text-xs tabular-nums text-muted">{zoom}%</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => stepZoom(1)}
            disabled={zoom === ZOOM_STEPS[ZOOM_STEPS.length - 1]}
            aria-label="Zoom in"
          >
            <ZoomIn size={15} />
          </Button>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {shown.map((doc) => (
            <a
              key={doc._id}
              href={doc.cloudinaryUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs font-medium text-muted transition hover:border-border-strong hover:text-foreground"
            >
              {doc.type === 'PDF' ? <FileText size={13} /> : <FileCode2 size={13} />}
              Open {doc.type}
              <ExternalLink size={12} />
            </a>
          ))}
        </div>
      </div>

      <div className={cn('grid min-h-0 flex-1 gap-3', shown.length > 1 && 'md:grid-cols-2')}>
        {shown.map((doc) => (
          <figure key={doc._id} className="flex min-h-0 flex-col gap-2">
            <figcaption className="flex items-center justify-between text-xs text-muted">
              <span className="font-medium text-foreground">{doc.type}</span>
              <span>{formatBytes(doc.size)}</span>
            </figcaption>
            <div className="min-h-[24rem] flex-1">
              <Frame doc={doc} zoom={zoom} />
            </div>
          </figure>
        ))}
      </div>
    </div>
  )
}
