'use client'

import { useEffect } from 'react'
import { AlertTriangle, Home, RotateCcw } from 'lucide-react'
import { Button, ButtonLink } from '@/components/ui/Button'

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error)
  }, [error])

  return (
    <div className="surface-panel mx-auto flex max-w-lg flex-col items-center px-6 py-16 text-center">
      <div className="mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-danger-soft text-danger ring-1 ring-inset ring-danger/15">
        <AlertTriangle size={26} />
      </div>
      <h1 className="text-lg font-semibold text-foreground">Something went wrong</h1>
      <p className="mt-1.5 max-w-sm text-sm text-muted">
        {error.message || 'An unexpected error interrupted this page.'}
      </p>
      {error.digest && <p className="mt-2 font-mono text-xs text-muted">Reference: {error.digest}</p>}
      <div className="mt-6 flex gap-2">
        <ButtonLink href="/" variant="secondary">
          <Home size={16} /> Dashboard
        </ButtonLink>
        <Button onClick={reset}>
          <RotateCcw size={16} /> Try again
        </Button>
      </div>
    </div>
  )
}
