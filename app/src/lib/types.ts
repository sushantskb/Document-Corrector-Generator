export type ProjectStatus = 'ACTIVE' | 'ARCHIVED'
export type JobStatus = 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
export type JobStage = 'ANALYZING_PDF' | 'ANALYZING_HTML' | 'COMPARING' | 'CORRECTING' | 'VERIFYING'
export type IssueSeverity = 'CRITICAL' | 'MAJOR' | 'MINOR' | 'INFO'
export type IssueStatus = 'AUTO_FIXED' | 'PENDING_REVIEW' | 'APPROVED' | 'REJECTED'
export type IssueType =
  | 'MISSING_TEXT'
  | 'EXTRA_TEXT'
  | 'TEXT_MISMATCH'
  | 'FORMATTING'
  | 'IMAGE_MISSING'
  | 'IMAGE_MISMATCH'
  | 'TABLE_STRUCTURE'
  | 'ORDER_MISMATCH'

export interface Project {
  _id: string
  name: string
  board: string
  standard: number
  subject: string
  language?: string
  status: ProjectStatus
  createdAt: string
  updatedAt?: string
  stats?: {
    documents: number
    jobs: number
    completedJobs: number
    pendingIssues: number
  }
}

export interface DocumentRecord {
  _id: string
  projectId: string
  type: 'PDF' | 'HTML'
  cloudinaryUrl: string
  cloudinaryPublicId: string
  originalName?: string
  mimeType: string
  size?: number
  version?: number
  status?: 'UPLOADED' | 'PROCESSING' | 'READY'
  createdAt: string
}

export interface JobLog {
  at: string
  level: 'INFO' | 'WARN' | 'ERROR'
  message: string
}

export interface Job {
  _id: string
  projectId: string
  pdfDocumentId: string
  htmlDocumentId: string
  status: JobStatus
  stage?: JobStage
  progress: number
  stats?: {
    issuesFound: number
    autoFixed: number
    pendingReview: number
    qualityScore?: number
  }
  logs?: JobLog[]
  correctedHtmlUrl?: string
  generatedHtmlUrl?: string
  imageMap?: { name: string; src: string; cdnUrl?: string; cdnUploaded?: boolean }[]
  imageUrlBase?: string
  imageStartNumber?: number
  startedAt?: string
  completedAt?: string
  error?: string
  createdAt: string
}

export interface Issue {
  _id: string
  projectId: string
  jobId: string
  type: IssueType
  severity: IssueSeverity
  status: IssueStatus
  confidence: number
  page?: number
  selector?: string
  message: string
  pdfText?: string
  htmlText?: string
  suggestion?: string
  createdAt: string
}

export interface Correction {
  _id: string
  projectId: string
  jobId: string
  issueId?: string
  selector?: string
  before: string
  after: string
  appliedBy: 'AUTO' | 'MANUAL'
  status: 'APPLIED' | 'REVERTED'
  createdAt: string
}

export interface JobReport {
  job: Job
  project: Project | null
  summary: {
    total: number
    autoFixed: number
    approved: number
    rejected: number
    pendingReview: number
    correctionsApplied: number
    qualityScore: number
  }
  byType: Record<IssueType, number>
  bySeverity: Record<IssueSeverity, number>
  byStatus: Record<IssueStatus, number>
  recommendations: string[]
}

export const ISSUE_TYPE_LABELS: Record<IssueType, string> = {
  MISSING_TEXT: 'Missing text',
  EXTRA_TEXT: 'Extra text',
  TEXT_MISMATCH: 'Text mismatch',
  FORMATTING: 'Formatting',
  IMAGE_MISSING: 'Missing image',
  IMAGE_MISMATCH: 'Image mismatch',
  TABLE_STRUCTURE: 'Table structure',
  ORDER_MISMATCH: 'Order mismatch',
}

export const ISSUE_STATUS_LABELS: Record<IssueStatus, string> = {
  AUTO_FIXED: 'Auto-fixed',
  PENDING_REVIEW: 'Pending review',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
}

export const JOB_STAGE_LABELS: Record<JobStage, string> = {
  ANALYZING_PDF: 'Analyzing PDF',
  ANALYZING_HTML: 'Analyzing HTML',
  COMPARING: 'Comparing',
  CORRECTING: 'Correcting',
  VERIFYING: 'Verifying',
}

export const JOB_STAGES: JobStage[] = [
  'ANALYZING_PDF',
  'ANALYZING_HTML',
  'COMPARING',
  'CORRECTING',
  'VERIFYING',
]
