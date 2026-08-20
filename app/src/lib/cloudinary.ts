import { v2 as cloudinary } from 'cloudinary'
import fs from 'fs'
import crypto from 'crypto'

cloudinary.config({
  cloud_name: process.env.CLOUDINARY_CLOUD_NAME,
  api_key: process.env.CLOUDINARY_API_KEY,
  api_secret: process.env.CLOUDINARY_API_SECRET,
})

export async function uploadFile(filePath: string, folder: string, publicId?: string) {
  try {
    const result = await cloudinary.uploader.upload(filePath, {
      folder: `projects/${folder}`,
      public_id: publicId,
      resource_type: 'auto',
    })

    return {
      publicId: result.public_id,
      url: result.secure_url,
      mimeType: result.resource_type,
      size: result.bytes,
    }
  } catch (error) {
    console.error('Cloudinary upload error:', error)
    throw error
  }
}

// uploadFile stores with resource_type 'auto', so the stored type varies by file
// (PDFs land as 'image', HTML as 'raw'). Destroy only matches on an exact type, so try each.
export async function deleteFile(publicId: string) {
  const types = ['raw', 'image', 'video'] as const

  for (const resourceType of types) {
    const result = await cloudinary.uploader.destroy(publicId, {
      resource_type: resourceType,
      invalidate: true,
    })
    if (result?.result === 'ok') return result
  }

  throw new Error(`Cloudinary asset ${publicId} not found under any resource type`)
}

export function calculateChecksum(filePath: string): string {
  const fileBuffer = fs.readFileSync(filePath)
  return crypto.createHash('sha256').update(fileBuffer).digest('hex')
}

export default cloudinary
