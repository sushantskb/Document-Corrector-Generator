import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Document, Issue, Job, Project } from '@/lib/models'

export async function POST(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()

    const project = new Project({
      name: body.name,
      board: body.board,
      standard: body.standard,
      subject: body.subject,
      language: body.language || 'EN',
    })

    await project.save()
    return NextResponse.json(project, { status: 201 })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to create project' }, { status: 500 })
  }
}

export async function GET(request: NextRequest) {
  try {
    await connectDB()
    const projects = await Project.find().sort({ createdAt: -1 }).lean()

    // The dashboard asks for ?stats=1 so each card can show counts without an N+1 fetch.
    if (request.nextUrl.searchParams.get('stats') !== '1') {
      return NextResponse.json(projects)
    }

    const [documents, jobs, issues] = await Promise.all([
      Document.aggregate([{ $group: { _id: '$projectId', count: { $sum: 1 } } }]),
      Job.aggregate([
        {
          $group: {
            _id: '$projectId',
            total: { $sum: 1 },
            completed: { $sum: { $cond: [{ $eq: ['$status', 'COMPLETED'] }, 1, 0] } },
          },
        },
      ]),
      Issue.aggregate([
        { $match: { status: 'PENDING_REVIEW' } },
        { $group: { _id: '$projectId', count: { $sum: 1 } } },
      ]),
    ])

    const documentMap = new Map(documents.map((row) => [String(row._id), row.count]))
    const jobMap = new Map(jobs.map((row) => [String(row._id), row]))
    const issueMap = new Map(issues.map((row) => [String(row._id), row.count]))

    const withStats = projects.map((project) => {
      const id = String(project._id)
      const jobRow = jobMap.get(id)
      return {
        ...project,
        stats: {
          documents: documentMap.get(id) ?? 0,
          jobs: jobRow?.total ?? 0,
          completedJobs: jobRow?.completed ?? 0,
          pendingIssues: issueMap.get(id) ?? 0,
        },
      }
    })

    return NextResponse.json(withStats)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch projects' }, { status: 500 })
  }
}
