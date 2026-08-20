import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Job } from '@/lib/models'

export async function POST(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()

    const job = new Job({
      projectId: body.projectId,
      pdfDocumentId: body.pdfDocumentId,
      htmlDocumentId: body.htmlDocumentId,
      status: 'QUEUED',
      progress: 0,
      startedAt: new Date(),
    })

    await job.save()
    return NextResponse.json(job, { status: 201 })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to create job' }, { status: 500 })
  }
}

export async function GET(request: NextRequest) {
  try {
    await connectDB()
    const jobId = request.nextUrl.searchParams.get('jobId')

    if (jobId) {
      const job = await Job.findById(jobId)
      if (!job) {
        return NextResponse.json({ error: 'Job not found' }, { status: 404 })
      }
      return NextResponse.json(job)
    }

    const projectId = request.nextUrl.searchParams.get('projectId')
    const query = projectId ? { projectId } : {}
    const jobs = await Job.find(query).sort({ createdAt: -1 })

    return NextResponse.json(jobs)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch jobs' }, { status: 500 })
  }
}
