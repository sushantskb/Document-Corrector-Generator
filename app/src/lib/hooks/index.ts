'use client'

import { useEffect, useState } from 'react'
import { useResource } from './useResource'
import type { Correction, DocumentRecord, Issue, Job, JobReport, Project } from '@/lib/types'

export { useResource } from './useResource'
export type { ResourceState } from './useResource'
export { useToast, ToastProvider } from './useToast'

export const useProject = (projectId?: string) =>
  useResource<Project>(projectId ? `/api/projects/${projectId}` : null)

export const useProjects = () => useResource<Project[]>('/api/projects?stats=1')

export const useDocuments = (projectId?: string) =>
  useResource<DocumentRecord[]>(projectId ? `/api/documents?projectId=${projectId}` : null)

export const useProjectJobs = (projectId?: string) =>
  useResource<Job[]>(projectId ? `/api/jobs?projectId=${projectId}` : null)

/** Polls every 2s while the job is still moving, then stops. */
export function useJob(jobId?: string) {
  const [pollMs, setPollMs] = useState<number | null>(2000)
  const resource = useResource<Job>(jobId ? `/api/jobs/${jobId}` : null, pollMs)
  const status = resource.data?.status

  useEffect(() => {
    const live = status === 'QUEUED' || status === 'PROCESSING'
    setPollMs(live ? 2000 : null)
  }, [status])

  return resource
}

export const useIssues = (jobId?: string) =>
  useResource<Issue[]>(jobId ? `/api/issues?jobId=${jobId}` : null)

export const useCorrections = (projectId?: string, jobId?: string) => {
  const query = jobId ? `jobId=${jobId}` : projectId ? `projectId=${projectId}` : null
  return useResource<Correction[]>(query ? `/api/corrections?${query}` : null)
}

export const useJobReport = (jobId?: string) =>
  useResource<JobReport>(jobId ? `/api/jobs/${jobId}/report` : null)
