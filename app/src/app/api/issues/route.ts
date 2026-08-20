import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Correction, Issue, ISSUE_STATUSES } from '@/lib/models'
import { requestRebuild } from '@/lib/processor'

type IssueStatus = (typeof ISSUE_STATUSES)[number]

/** One review decision may cover hundreds of issues; refuse absurd payloads. */
const MAX_BULK = 5000

/** Must match AUTO_FIX_CONFIDENCE in the processing service. */
const AUTO_FIX_CONFIDENCE = Number(process.env.AUTO_FIX_CONFIDENCE ?? 0.95)

/**
 * The state an issue was in before anyone reviewed it.
 *
 * Resetting is meant to undo *human* decisions, not the engine's. Setting
 * everything to "pending review" would wrongly claim the engine never fixed
 * anything, so each issue goes back to whichever verdict the engine reached:
 * auto-fixed if it carried a fix confident enough to apply unattended.
 */
function engineVerdict(issue: { confidence?: number; engine?: { autoFixable?: boolean } }): IssueStatus {
  const autoFixable = issue.engine?.autoFixable === true
  const confident = (issue.confidence ?? 0) >= AUTO_FIX_CONFIDENCE
  return autoFixable && confident ? 'AUTO_FIXED' : 'PENDING_REVIEW'
}

export async function GET(request: NextRequest) {
  try {
    await connectDB()
    const sp = request.nextUrl.searchParams
    const query: Record<string, unknown> = {}

    for (const key of ['jobId', 'projectId', 'type', 'severity', 'status'] as const) {
      const value = sp.get(key)
      if (value) query[key] = value
    }

    const issues = await Issue.find(query).sort({ severity: 1, createdAt: 1 })
    return NextResponse.json(issues)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch issues' }, { status: 500 })
  }
}

// Accepts a single issue or an array — the processing service reports issues in batches.
export async function POST(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()
    const payload = Array.isArray(body) ? body : [body]

    const issues = await Issue.insertMany(payload)
    return NextResponse.json(issues, { status: 201 })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to create issues' }, { status: 500 })
  }
}

/**
 * Applies one decision to many issues at once.
 *
 * Doing this row by row would mean a request and a document rebuild per issue —
 * hundreds of each for a single click. Here the statuses move in one write, the
 * correction records follow, and each affected job is rebuilt exactly once.
 */
export async function PATCH(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()
    const status = body.status as IssueStatus
    const ids: string[] = Array.isArray(body.ids) ? body.ids : []

    if (!ISSUE_STATUSES.includes(status)) {
      return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
    }
    if (ids.length === 0) {
      return NextResponse.json({ error: 'No issues given' }, { status: 400 })
    }
    if (ids.length > MAX_BULK) {
      return NextResponse.json(
        { error: `Too many issues in one request (max ${MAX_BULK})` },
        { status: 413 }
      )
    }

    const issues = await Issue.find({ _id: { $in: ids } })
    if (issues.length === 0) {
      return NextResponse.json({ error: 'No matching issues' }, { status: 404 })
    }

    if (status === 'PENDING_REVIEW') {
      // A reset restores each issue's engine verdict and drops the record of
      // any manual correction, so the queue looks as it did after processing.
      await Issue.bulkWrite(
        issues.map((issue) => ({
          updateOne: {
            filter: { _id: issue._id },
            update: { $set: { status: engineVerdict(issue.toObject()) } },
          },
        }))
      )
      await Correction.updateMany(
        { issueId: { $in: issues.map((i) => i._id) }, appliedBy: 'MANUAL' },
        { status: 'REVERTED' }
      )
      const resetJobIds = [...new Set(issues.map((issue) => String(issue.jobId)))]
      const resetRebuilds = await Promise.all(resetJobIds.map((jobId) => requestRebuild(jobId)))
      return NextResponse.json({
        updated: issues.length,
        status,
        rebuild: resetRebuilds.includes('started') ? 'started' : resetRebuilds[0] ?? 'unavailable',
      })
    }

    await Issue.updateMany({ _id: { $in: issues.map((i) => i._id) } }, { status })

    if (status === 'APPROVED') {
      const operations = issues
        .filter((issue) => issue.suggestion)
        .map((issue) => ({
          updateOne: {
            filter: { issueId: issue._id },
            update: {
              $set: {
                projectId: issue.projectId,
                jobId: issue.jobId,
                issueId: issue._id,
                selector: issue.selector,
                before: issue.htmlText || '',
                after: issue.suggestion,
                appliedBy: 'MANUAL',
                status: 'APPLIED',
              },
            },
            upsert: true,
          },
        }))
      if (operations.length > 0) await Correction.bulkWrite(operations)
    } else if (status === 'REJECTED') {
      await Correction.updateMany({ issueId: { $in: issues.map((i) => i._id) } }, { status: 'REVERTED' })
    }

    // One rebuild per affected job, however many issues were decided.
    const jobIds = [...new Set(issues.map((issue) => String(issue.jobId)))]
    const rebuilds = await Promise.all(jobIds.map((jobId) => requestRebuild(jobId)))

    return NextResponse.json({
      updated: issues.length,
      status,
      rebuild: rebuilds.includes('started') ? 'started' : rebuilds[0] ?? 'unavailable',
    })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to update issues' }, { status: 500 })
  }
}
