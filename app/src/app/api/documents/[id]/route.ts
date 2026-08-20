import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { Document } from '@/lib/models'
import { deleteFile } from '@/lib/cloudinary'

export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const doc = await Document.findById(params.id)

    if (!doc) {
      return NextResponse.json({ error: 'Document not found' }, { status: 404 })
    }

    return NextResponse.json(doc)
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to fetch document' }, { status: 500 })
  }
}

export async function DELETE(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const doc = await Document.findById(params.id)

    if (!doc) {
      return NextResponse.json({ error: 'Document not found' }, { status: 404 })
    }

    // Remove the stored asset first; a failure there must not orphan the DB record silently.
    try {
      await deleteFile(doc.cloudinaryPublicId)
    } catch (error) {
      console.error('Cloudinary delete failed:', error)
    }

    await doc.deleteOne()
    return NextResponse.json({ success: true })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to delete document' }, { status: 500 })
  }
}
