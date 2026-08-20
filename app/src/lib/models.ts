import mongoose, { Schema, Document as MongooseDocument } from 'mongoose'

export const JOB_STAGES = ['ANALYZING_PDF', 'ANALYZING_HTML', 'COMPARING', 'CORRECTING', 'VERIFYING'] as const
export type JobStage = (typeof JOB_STAGES)[number]

export const ISSUE_TYPES = [
  'MISSING_TEXT',
  'EXTRA_TEXT',
  'TEXT_MISMATCH',
  'FORMATTING',
  'IMAGE_MISSING',
  'IMAGE_MISMATCH',
  'TABLE_STRUCTURE',
  'ORDER_MISMATCH',
] as const
export type IssueType = (typeof ISSUE_TYPES)[number]

export const ISSUE_SEVERITIES = ['CRITICAL', 'MAJOR', 'MINOR', 'INFO'] as const
export type IssueSeverity = (typeof ISSUE_SEVERITIES)[number]

export const ISSUE_STATUSES = ['AUTO_FIXED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED'] as const
export type IssueStatus = (typeof ISSUE_STATUSES)[number]

export interface IProject extends MongooseDocument {
  name: string
  board: string
  standard: number
  subject: string
  language: string
  status: 'ACTIVE' | 'ARCHIVED'
  createdAt: Date
  updatedAt: Date
}

export interface IDocument extends MongooseDocument {
  projectId: mongoose.Types.ObjectId
  type: 'PDF' | 'HTML'
  language: string
  cloudinaryPublicId: string
  cloudinaryUrl: string
  originalName?: string
  mimeType: string
  size: number
  checksum: string
  version: number
  status: 'UPLOADED' | 'PROCESSING' | 'READY'
  createdAt: Date
  updatedAt: Date
}

export interface IJobLog {
  at: Date
  level: 'INFO' | 'WARN' | 'ERROR'
  message: string
}

export interface IJob extends MongooseDocument {
  projectId: mongoose.Types.ObjectId
  pdfDocumentId: mongoose.Types.ObjectId
  htmlDocumentId: mongoose.Types.ObjectId
  status: 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  stage?: JobStage
  progress: number
  stats: {
    issuesFound: number
    autoFixed: number
    pendingReview: number
    qualityScore?: number
  }
  logs: IJobLog[]
  /** Where the processing service published the corrected HTML. */
  correctedHtmlUrl?: string
  /** A fresh HTML rendition generated straight from the PDF. */
  generatedHtmlUrl?: string
  startedAt: Date
  completedAt?: Date
  error?: string
  createdAt: Date
  updatedAt: Date
}

export interface IIssue extends MongooseDocument {
  projectId: mongoose.Types.ObjectId
  jobId: mongoose.Types.ObjectId
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
  createdAt: Date
  updatedAt: Date
}

export interface ICorrection extends MongooseDocument {
  projectId: mongoose.Types.ObjectId
  jobId: mongoose.Types.ObjectId
  issueId?: mongoose.Types.ObjectId
  selector?: string
  before: string
  after: string
  appliedBy: 'AUTO' | 'MANUAL'
  status: 'APPLIED' | 'REVERTED'
  createdAt: Date
  updatedAt: Date
}

const ProjectSchema = new Schema<IProject>(
  {
    name: { type: String, required: true },
    board: { type: String, required: true },
    standard: { type: Number, required: true },
    subject: { type: String, required: true },
    language: { type: String, default: 'EN' },
    status: { type: String, default: 'ACTIVE', enum: ['ACTIVE', 'ARCHIVED'] },
  },
  { timestamps: true }
)

const DocumentSchema = new Schema<IDocument>(
  {
    projectId: { type: Schema.Types.ObjectId, ref: 'Project', required: true, index: true },
    type: { type: String, required: true, enum: ['PDF', 'HTML'] },
    language: { type: String, default: 'EN' },
    cloudinaryPublicId: { type: String, required: true },
    cloudinaryUrl: { type: String, required: true },
    originalName: { type: String },
    mimeType: { type: String, required: true },
    size: { type: Number, required: true },
    checksum: { type: String, required: true },
    version: { type: Number, default: 1 },
    status: { type: String, default: 'UPLOADED', enum: ['UPLOADED', 'PROCESSING', 'READY'] },
  },
  { timestamps: true }
)

const JobLogSchema = new Schema<IJobLog>(
  {
    at: { type: Date, default: Date.now },
    level: { type: String, default: 'INFO', enum: ['INFO', 'WARN', 'ERROR'] },
    message: { type: String, required: true },
  },
  { _id: false }
)

const JobSchema = new Schema<IJob>(
  {
    projectId: { type: Schema.Types.ObjectId, ref: 'Project', required: true, index: true },
    pdfDocumentId: { type: Schema.Types.ObjectId, ref: 'Document', required: true },
    htmlDocumentId: { type: Schema.Types.ObjectId, ref: 'Document', required: true },
    status: {
      type: String,
      default: 'QUEUED',
      enum: ['QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED'],
    },
    stage: { type: String, enum: JOB_STAGES },
    progress: { type: Number, default: 0, min: 0, max: 100 },
    stats: {
      issuesFound: { type: Number, default: 0 },
      autoFixed: { type: Number, default: 0 },
      pendingReview: { type: Number, default: 0 },
      qualityScore: { type: Number },
    },
    logs: { type: [JobLogSchema], default: [] },
    correctedHtmlUrl: { type: String },
    generatedHtmlUrl: { type: String },
    startedAt: { type: Date },
    completedAt: { type: Date },
    error: { type: String },
  },
  { timestamps: true }
)

const IssueSchema = new Schema<IIssue>(
  {
    projectId: { type: Schema.Types.ObjectId, ref: 'Project', required: true, index: true },
    jobId: { type: Schema.Types.ObjectId, ref: 'Job', required: true, index: true },
    type: { type: String, required: true, enum: ISSUE_TYPES },
    severity: { type: String, required: true, enum: ISSUE_SEVERITIES },
    status: { type: String, default: 'PENDING_REVIEW', enum: ISSUE_STATUSES },
    confidence: { type: Number, default: 0, min: 0, max: 1 },
    page: { type: Number },
    selector: { type: String },
    message: { type: String, required: true },
    pdfText: { type: String },
    htmlText: { type: String },
    suggestion: { type: String },
  },
  { timestamps: true }
)

const CorrectionSchema = new Schema<ICorrection>(
  {
    projectId: { type: Schema.Types.ObjectId, ref: 'Project', required: true, index: true },
    jobId: { type: Schema.Types.ObjectId, ref: 'Job', required: true, index: true },
    issueId: { type: Schema.Types.ObjectId, ref: 'Issue' },
    selector: { type: String },
    before: { type: String, default: '' },
    after: { type: String, default: '' },
    appliedBy: { type: String, default: 'AUTO', enum: ['AUTO', 'MANUAL'] },
    status: { type: String, default: 'APPLIED', enum: ['APPLIED', 'REVERTED'] },
  },
  { timestamps: true }
)

export const Project = mongoose.models.Project || mongoose.model<IProject>('Project', ProjectSchema)
export const Document = mongoose.models.Document || mongoose.model<IDocument>('Document', DocumentSchema)
export const Job = mongoose.models.Job || mongoose.model<IJob>('Job', JobSchema)
export const Issue = mongoose.models.Issue || mongoose.model<IIssue>('Issue', IssueSchema)
export const Correction =
  mongoose.models.Correction || mongoose.model<ICorrection>('Correction', CorrectionSchema)
