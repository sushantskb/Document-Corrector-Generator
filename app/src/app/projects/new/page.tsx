'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ArrowLeft, Sparkles } from 'lucide-react'
import { Alert } from '@/components/ui/Alert'
import { Button } from '@/components/ui/Button'
import { Field, SegmentedControl } from '@/components/ui/Field'
import { apiJson } from '@/lib/api'
import { useToast } from '@/lib/hooks'
import type { Project } from '@/lib/types'
import { cn } from '@/lib/utils'

const BOARDS = [
  { value: 'CBSE', label: 'CBSE' },
  { value: 'ICSE', label: 'ICSE' },
  { value: 'STATE_BOARD', label: 'State Board' },
]

const LANGUAGES = [
  { value: 'EN', label: 'English' },
  { value: 'ML', label: 'Malayalam' },
  { value: 'HI', label: 'Hindi' },
]

const STANDARDS = [8, 9, 10, 11, 12]

const TEMPLATES = [
  { label: 'CBSE Class 10 Maths', board: 'CBSE', standard: 10, subject: 'Mathematics', language: 'EN' },
  { label: 'CBSE Class 12 Physics', board: 'CBSE', standard: 12, subject: 'Physics', language: 'EN' },
  { label: 'Kerala Class 9 Science', board: 'STATE_BOARD', standard: 9, subject: 'Science', language: 'ML' },
  { label: 'ICSE Class 8 Biology', board: 'ICSE', standard: 8, subject: 'Biology', language: 'EN' },
]

interface FormState {
  name: string
  board: string
  standard: number
  subject: string
  language: string
}

type FieldErrors = Partial<Record<'name' | 'subject', string>>

function validate(form: FormState): FieldErrors {
  const errors: FieldErrors = {}

  if (!form.name.trim()) errors.name = 'Give the project a name.'
  else if (form.name.trim().length < 3) errors.name = 'Use at least 3 characters.'

  if (!form.subject.trim()) errors.subject = 'Name the subject this book covers.'

  return errors
}

export default function NewProject() {
  const router = useRouter()
  const toast = useToast()

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [form, setForm] = useState<FormState>({
    name: '',
    board: 'CBSE',
    standard: 8,
    subject: '',
    language: 'EN',
  })

  const errors = validate(form)
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))

  const applyTemplate = (template: (typeof TEMPLATES)[number]) => {
    setForm({
      name: form.name || template.label,
      board: template.board,
      standard: template.standard,
      subject: template.subject,
      language: template.language,
    })
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setTouched({ name: true, subject: true })

    if (Object.keys(errors).length > 0) return

    setLoading(true)
    setError(null)

    try {
      const project = await apiJson<Project>('/api/projects', 'POST', {
        ...form,
        name: form.name.trim(),
        subject: form.subject.trim(),
      })
      toast.success('Project created', `${project.name} is ready for uploads.`)
      router.push(`/projects/${project._id}`)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Request failed'
      setError(message)
      toast.error('Could not create the project', message)
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-muted transition hover:text-foreground"
      >
        <ArrowLeft size={16} /> Back to projects
      </Link>

      <div className="surface-panel animate-fade-up overflow-hidden">
        <div className="border-b border-border bg-surface-muted/50 px-7 py-6">
          <div className="flex items-center gap-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-primary-soft text-primary ring-1 ring-inset ring-primary/15">
              <Sparkles size={19} />
            </span>
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-foreground">Create a new project</h1>
              <p className="mt-0.5 text-sm text-muted">
                Group a textbook&apos;s PDF and HTML so they can be compared.
              </p>
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit} noValidate className="space-y-6 px-7 py-7">
          {error && (
            <Alert tone="error" title="Something went wrong" onDismiss={() => setError(null)}>
              {error}
            </Alert>
          )}

          <div>
            <p className="mb-2 text-sm font-medium text-foreground">Start from a template</p>
            <div className="flex flex-wrap gap-1.5">
              {TEMPLATES.map((template) => (
                <button
                  key={template.label}
                  type="button"
                  onClick={() => applyTemplate(template)}
                  className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted transition hover:border-primary/40 hover:text-primary"
                >
                  {template.label}
                </button>
              ))}
            </div>
          </div>

          <Field label="Project name" htmlFor="name" hint="A short label you'll recognise in the dashboard.">
            <input
              id="name"
              name="name"
              type="text"
              autoFocus
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
              aria-invalid={Boolean(touched.name && errors.name)}
              aria-describedby={touched.name && errors.name ? 'name-error' : undefined}
              className={cn('field-input', touched.name && errors.name && 'border-danger focus:border-danger focus:ring-danger/25')}
              placeholder="e.g. Grade 8 Mathematics"
            />
            {touched.name && errors.name && (
              <p id="name-error" role="alert" className="text-xs text-danger">
                {errors.name}
              </p>
            )}
          </Field>

          <Field label="Board">
            <SegmentedControl name="board" value={form.board} options={BOARDS} onChange={(value) => set('board', value)} />
          </Field>

          <div className="grid gap-6 sm:grid-cols-2">
            <Field label="Standard" htmlFor="standard">
              <select
                id="standard"
                name="standard"
                value={form.standard}
                onChange={(e) => set('standard', Number(e.target.value))}
                className="field-input"
              >
                {STANDARDS.map((std) => (
                  <option key={std} value={std}>
                    Class {std}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Language" htmlFor="language" hint="Language of the source textbook.">
              <select
                id="language"
                name="language"
                value={form.language}
                onChange={(e) => set('language', e.target.value)}
                className="field-input"
              >
                {LANGUAGES.map((lang) => (
                  <option key={lang.value} value={lang.value}>
                    {lang.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <Field label="Subject" htmlFor="subject">
            <input
              id="subject"
              name="subject"
              type="text"
              value={form.subject}
              onChange={(e) => set('subject', e.target.value)}
              onBlur={() => setTouched((prev) => ({ ...prev, subject: true }))}
              aria-invalid={Boolean(touched.subject && errors.subject)}
              aria-describedby={touched.subject && errors.subject ? 'subject-error' : undefined}
              className={cn('field-input', touched.subject && errors.subject && 'border-danger focus:border-danger focus:ring-danger/25')}
              placeholder="e.g. Mathematics"
            />
            {touched.subject && errors.subject && (
              <p id="subject-error" role="alert" className="text-xs text-danger">
                {errors.subject}
              </p>
            )}
          </Field>

          <div className="flex flex-col-reverse gap-3 border-t border-border pt-6 sm:flex-row sm:justify-end">
            <Button type="button" variant="secondary" size="lg" onClick={() => router.back()}>
              Cancel
            </Button>
            <Button type="submit" size="lg" loading={loading}>
              {loading ? 'Creating…' : 'Create project'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
