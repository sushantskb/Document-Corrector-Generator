import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Correction, Issue, ISSUE_STATUSES } from '@/lib/models'
import { requestRebuild } from '@/lib/processor'

type IssueStatus = (typeof ISSUE_STATUSES)[number]

export async function PATCH(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const body = await request.json()
    const status = body.status as IssueStatus

    if (!ISSUE_STATUSES.includes(status)) {
      return NextResponse.json({ error: 'Invalid status' }, { status: 400 })
    }

    const issue = await Issue.findByIdAndUpdate(params.id, { status }, { new: true })
    if (!issue) {
      return NextResponse.json({ error: 'Issue not found' }, { status: 404 })
    }

    // Approving records the correction; rejecting reverts any correction already logged for it.
    if (status === 'APPROVED' && issue.suggestion) {
      await Correction.findOneAndUpdate(
        { issueId: issue._id },
        {
          projectId: issue.projectId,
          jobId: issue.jobId,
          issueId: issue._id,
          selector: issue.selector,
          before: issue.htmlText || '',
          after: issue.suggestion,
          appliedBy: 'MANUAL',
          status: 'APPLIED',
        },
        { upsert: true, new: true }
      )
    } else if (status === 'REJECTED') {
      await Correction.updateMany({ issueId: issue._id }, { status: 'REVERTED' })
    }

    // The corrected HTML is generated from every current decision, so it has to
    // be rebuilt whenever one changes.
    const rebuild = await requestRebuild(String(issue.jobId))

    return NextResponse.json({ ...issue.toObject(), rebuild })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to update issue' }, { status: 500 })
  }
}

export async function DELETE(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const issue = await Issue.findByIdAndDelete(params.id)

    if (!issue) {
      return NextResponse.json({ error: 'Issue not found' }, { status: 404 })
    }

    return NextResponse.json({ success: true })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to delete issue' }, { status: 500 })
  }
}
