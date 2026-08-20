'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ChevronRight, Home } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'

interface Crumb {
  label: string
  href?: string
}

/**
 * Derives breadcrumbs from the URL. The project segment is an opaque ObjectId,
 * so it is swapped for the project name once that resolves.
 */
function buildCrumbs(pathname: string, projectName?: string): Crumb[] {
  const parts = pathname.split('/').filter(Boolean)
  if (parts.length === 0) return []

  const crumbs: Crumb[] = [{ label: 'Projects', href: '/' }]

  if (parts[0] !== 'projects') return crumbs

  if (parts[1] === 'new') {
    crumbs.push({ label: 'New project' })
    return crumbs
  }

  const projectId = parts[1]
  if (!projectId) return crumbs

  const isProjectLeaf = parts.length === 2
  crumbs.push({
    label: projectName || 'Project',
    href: isProjectLeaf ? undefined : `/projects/${projectId}`,
  })

  if (parts[2] === 'corrections') {
    crumbs.push({ label: 'Corrections' })
    return crumbs
  }

  if (parts[2] === 'jobs' && parts[3]) {
    const jobHref = `/projects/${projectId}/jobs/${parts[3]}`
    const isJobLeaf = parts.length === 4
    crumbs.push({ label: `Job ${parts[3].slice(-6)}`, href: isJobLeaf ? undefined : jobHref })

    if (parts[4] === 'issues') crumbs.push({ label: 'Issues' })
    if (parts[4] === 'report') crumbs.push({ label: 'Report' })
  }

  return crumbs
}

export function Breadcrumbs() {
  const pathname = usePathname()
  const [projectName, setProjectName] = useState<string>()

  const projectId = pathname.startsWith('/projects/') ? pathname.split('/')[2] : undefined

  useEffect(() => {
    if (!projectId || projectId === 'new') {
      setProjectName(undefined)
      return
    }

    let cancelled = false
    api<Project>(`/api/projects/${projectId}`)
      .then((project) => {
        if (!cancelled) setProjectName(project.name)
      })
      .catch(() => {
        if (!cancelled) setProjectName(undefined)
      })

    return () => {
      cancelled = true
    }
  }, [projectId])

  const crumbs = buildCrumbs(pathname, projectName)
  if (crumbs.length <= 1) return null

  return (
    <nav aria-label="Breadcrumb" className="border-b border-border bg-surface/60">
      <ol className="mx-auto flex max-w-7xl flex-wrap items-center gap-1.5 px-4 py-2.5 text-xs sm:px-6 lg:px-8">
        <li>
          <Link href="/" aria-label="Home" className="grid h-5 w-5 place-items-center rounded text-muted transition hover:text-foreground">
            <Home size={13} />
          </Link>
        </li>
        {crumbs.map((crumb, index) => (
          <li key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
            <ChevronRight size={12} className="text-muted/60" aria-hidden="true" />
            {crumb.href ? (
              <Link href={crumb.href} className="text-muted transition hover:text-foreground">
                {crumb.label}
              </Link>
            ) : (
              <span aria-current="page" className="font-medium text-foreground">
                {crumb.label}
              </span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  )
}
