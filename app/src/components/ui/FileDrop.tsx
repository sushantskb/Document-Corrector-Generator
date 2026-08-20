'use client'

import { useRef, useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import { UploadCloud, X } from 'lucide-react'
import { cn, formatBytes } from '@/lib/utils'

export function FileDrop({
  label,
  accept,
  hint,
  file,
  onSelect,
  icon: Icon = UploadCloud,
}: {
  label: string
  accept: string
  hint?: string
  file: File | null
  onSelect: (file: File | null) => void
  icon?: LucideIcon
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files?.[0]
    if (dropped) onSelect(dropped)
  }

  if (file) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-primary/30 bg-primary-soft/60 px-4 py-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-surface text-primary shadow-sm">
          <Icon size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">{file.name}</p>
          <p className="text-xs text-muted">
            {label} · {formatBytes(file.size)}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onSelect(null)}
          aria-label={`Remove ${label}`}
          className="shrink-0 rounded-lg p-1.5 text-muted transition hover:bg-surface hover:text-danger"
        >
          <X size={16} />
        </button>
      </div>
    )
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          inputRef.current?.click()
        }
      }}
      role="button"
      tabIndex={0}
      className={cn(
        'flex cursor-pointer flex-col items-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition',
        dragging
          ? 'border-primary bg-primary-soft'
          : 'border-border bg-surface-muted/50 hover:border-border-strong hover:bg-surface-muted'
      )}
    >
      <Icon size={22} className={cn('mb-2.5 transition', dragging ? 'text-primary' : 'text-muted')} />
      <p className="text-sm font-medium text-foreground">
        Drop {label} here or <span className="text-primary">browse</span>
      </p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
      />
    </div>
  )
}
