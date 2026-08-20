import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Job } from '@/lib/models'

export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const job = await Job.findById(params.id)

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }

    return NextResponse.json(job)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch job' }, { status: 500 })
  }
}

// Used to cancel a running job, or to retry a failed one by requeueing it.
export async function PATCH(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const body = await request.json()
    const job = await Job.findById(params.id)

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }

    if (body.action === 'cancel') {
      if (job.status === 'COMPLETED' || job.status === 'CANCELLED') {
        return NextResponse.json({ error: `Job is already ${job.status.toLowerCase()}` }, { status: 409 })
      }
      job.status = 'CANCELLED'
      job.completedAt = new Date()
      job.logs.push({ at: new Date(), level: 'WARN', message: 'Job cancelled by user' })
    } else if (body.action === 'retry') {
      if (job.status !== 'FAILED' && job.status !== 'CANCELLED') {
        return NextResponse.json({ error: 'Only failed or cancelled jobs can be retried' }, { status: 409 })
      }
      job.status = 'QUEUED'
      job.stage = undefined
      job.progress = 0
      job.error = undefined
      job.completedAt = undefined
      job.startedAt = new Date()
      job.logs.push({ at: new Date(), level: 'INFO', message: 'Job requeued by user' })
    } else {
      return NextResponse.json({ error: 'Unsupported action' }, { status: 400 })
    }

    await job.save()
    return NextResponse.json(job)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to update job' }, { status: 500 })
  }
}

export async function DELETE(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const job = await Job.findByIdAndDelete(params.id)

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }

    return NextResponse.json({ success: true })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to delete job' }, { status: 500 })
  }
}
