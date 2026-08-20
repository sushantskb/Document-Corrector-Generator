'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { FileCheck2, Plus } from 'lucide-react'
import { ThemeToggle } from '@/components/ThemeToggle'
import { ButtonLink } from '@/components/ui/Button'
import { cn } from '@/lib/utils'

export function Header() {
  const pathname = usePathname()
  const onDashboard = pathname === '/'

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-surface/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="group flex items-center gap-2.5">
          <span className="grid h-9 w-9 place-items-center rounded-xl bg-primary-fill text-white shadow-sm transition group-hover:scale-105">
            <FileCheck2 size={18} />
          </span>
          <span className="hidden flex-col leading-none sm:flex">
            <span className="text-sm font-semibold tracking-tight text-foreground">Document Correction</span>
            <span className="mt-0.5 text-xs text-muted">PDF → HTML verification</span>
          </span>
        </Link>

        <nav aria-label="Main" className="ml-auto flex items-center gap-1">
          <Link
            href="/"
            aria-current={onDashboard ? 'page' : undefined}
            className={cn(
              'rounded-lg px-3 py-1.5 text-sm font-medium transition',
              onDashboard ? 'bg-surface-muted text-foreground' : 'text-muted hover:bg-surface-muted hover:text-foreground'
            )}
          >
            Projects
          </Link>
          <ButtonLink href="/projects/new" size="sm" className="hidden sm:inline-flex">
            <Plus size={15} /> New
          </ButtonLink>
          <ThemeToggle />
        </nav>
      </div>
    </header>
  )
}
