import { NextRequest, NextResponse } from 'next/server'
import { writeFileSync, unlinkSync } from 'fs'
import { uploadFile, calculateChecksum } from '@/lib/cloudinary'
import path from 'path'

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData()
    const file = formData.get('file') as File
    const folder = formData.get('folder') as string

    if (!file) {
      return NextResponse.json({ error: 'No file provided' }, { status: 400 })
    }

    // Save temp file
    const bytes = await file.arrayBuffer()
    const buffer = Buffer.from(bytes)
    const tempPath = path.join('/tmp', Date.now().toString() + file.name)
    
    writeFileSync(tempPath, buffer)

    try {
      // Upload to Cloudinary
      const uploadResult = await uploadFile(tempPath, folder || 'uploads')
      const checksum = calculateChecksum(tempPath)

      return NextResponse.json({
        cloudinaryPublicId: uploadResult.publicId,
        cloudinaryUrl: uploadResult.url,
        mimeType: uploadResult.mimeType,
        size: uploadResult.size,
        checksum,
      })
    } finally {
      // Clean up temp file
      try {
        unlinkSync(tempPath)
      } catch (e) {
        console.error('Failed to delete temp file:', e)
      }
    }
  } catch (error) {
    console.error('Upload error:', error)
    return NextResponse.json({ error: 'Upload failed' }, { status: 500 })
  }
}
