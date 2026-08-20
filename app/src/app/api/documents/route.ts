import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Document } from '@/lib/models'

export async function GET(request: NextRequest) {
  try {
    await connectDB()
    const projectId = request.nextUrl.searchParams.get('projectId')

    const query = projectId ? { projectId } : {}
    const documents = await Document.find(query).sort({ createdAt: -1 })

    return NextResponse.json(documents)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch documents' }, { status: 500 })
  }
}

export async function POST(request: NextRequest) {
  try {
    await connectDB()
    const body = await request.json()

    const doc = new Document({
      projectId: body.projectId,
      type: body.type,
      language: body.language || 'EN',
      cloudinaryPublicId: body.cloudinaryPublicId,
      cloudinaryUrl: body.cloudinaryUrl,
      mimeType: body.mimeType,
      size: body.size,
      checksum: body.checksum,
    })

    await doc.save()
    return NextResponse.json(doc, { status: 201 })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to create document' }, { status: 500 })
  }
}
