import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Correction } from '@/lib/models'

export async function GET(request: NextRequest) {
  try {
    await connectDB()
    const sp = request.nextUrl.searchParams
    const query: Record<string, unknown> = {}

    for (const key of ['jobId', 'projectId', 'status'] as const) {
      const value = sp.get(key)
      if (value) query[key] = value
    }

    const corrections = await Correction.find(query).sort({ createdAt: -1 })
    return NextResponse.json(corrections)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch corrections' }, { status: 500 })
  }
}

export async function POST(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()
    const payload = Array.isArray(body) ? body : [body]

    const corrections = await Correction.insertMany(payload)
    return NextResponse.json(corrections, { status: 201 })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to create corrections' }, { status: 500 })
  }
}

export async function PATCH(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()

    if (!body.id || !['APPLIED', 'REVERTED'].includes(body.status)) {
      return NextResponse.json({ error: 'id and a valid status are required' }, { status: 400 })
    }

    const correction = await Correction.findByIdAndUpdate(body.id, { status: body.status }, { new: true })
    if (!correction) {
      return NextResponse.json({ error: 'Correction not found' }, { status: 404 })
    }

    return NextResponse.json(correction)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to update correction' }, { status: 500 })
  }
}
