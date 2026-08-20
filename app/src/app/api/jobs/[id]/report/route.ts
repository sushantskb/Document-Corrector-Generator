import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Correction, Issue, Job, Project } from '@/lib/models'
import { ISSUE_SEVERITIES, ISSUE_STATUSES, ISSUE_TYPES } from '@/lib/models'

const SEVERITY_WEIGHT: Record<string, number> = { CRITICAL: 10, MAJOR: 4, MINOR: 1, INFO: 0 }

function countBy<T extends string>(rows: Array<{ [k: string]: unknown }>, key: string, keys: readonly T[]) {
  const counts = Object.fromEntries(keys.map((k) => [k, 0])) as Record<T, number>
  for (const row of rows) {
    const value = row[key] as T
    if (value in counts) counts[value] += 1
  }
  return counts
}

export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()

    const job = await Job.findById(params.id)
    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }

    const [project, issues, corrections] = await Promise.all([
      Project.findById(job.projectId),
      Issue.find({ jobId: job._id }).lean(),
      Correction.countDocuments({ jobId: job._id, status: 'APPLIED' }),
    ])

    const byType = countBy(issues, 'type', ISSUE_TYPES)
    const bySeverity = countBy(issues, 'severity', ISSUE_SEVERITIES)
    const byStatus = countBy(issues, 'status', ISSUE_STATUSES)

    // 100 minus weighted severity penalties, floored at 0.
    const penalty = Object.entries(bySeverity).reduce(
      (sum, [severity, count]) => sum + (SEVERITY_WEIGHT[severity] ?? 0) * count,
      0
    )
    const qualityScore = Math.max(0, 100 - penalty)

    const recommendations: string[] = []
    if (bySeverity.CRITICAL > 0) {
      recommendations.push(
        `${bySeverity.CRITICAL} critical issue${bySeverity.CRITICAL === 1 ? '' : 's'} need manual review before this conversion ships.`
      )
    }
    if (byStatus.PENDING_REVIEW > 0) {
      recommendations.push(`${byStatus.PENDING_REVIEW} correction${byStatus.PENDING_REVIEW === 1 ? '' : 's'} are waiting on an approve/reject decision.`)
    }
    if (byType.IMAGE_MISSING > 0) {
      recommendations.push(`${byType.IMAGE_MISSING} image${byType.IMAGE_MISSING === 1 ? '' : 's'} present in the PDF are absent from the HTML.`)
    }
    if (byType.TABLE_STRUCTURE > 0) {
      recommendations.push('Table structure differs between source and output — verify row and column counts.')
    }
    if (issues.length === 0) {
      recommendations.push('No issues detected. The HTML matches the source PDF.')
    }

    return NextResponse.json({
      job,
      project,
      summary: {
        total: issues.length,
        autoFixed: byStatus.AUTO_FIXED,
        approved: byStatus.APPROVED,
        rejected: byStatus.REJECTED,
        pendingReview: byStatus.PENDING_REVIEW,
        correctionsApplied: corrections,
        qualityScore,
      },
      byType,
      bySeverity,
      byStatus,
      recommendations,
    })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to build report' }, { status: 500 })
  }
}
