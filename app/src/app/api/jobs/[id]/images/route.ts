import { NextRequest, NextResponse } from 'next/server'

const PROCESSOR_URL = process.env.PYTHON_PROCESSOR_URL || 'http://localhost:8000'

/**
 * Streams the zip of images this job added, named per the delivery convention
 * (kerla_new_NN.png). The team uploads these files to the CDN bucket so the
 * deliverable HTML's image URLs resolve.
 */
export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    const upstream = await fetch(`${PROCESSOR_URL}/jobs/${params.id}/image-bundle`, {
      signal: AbortSignal.timeout(120000),
    })
    if (!upstream.ok) {
      const detail = await upstream.json().catch(() => null)
      return NextResponse.json(
        { error: detail?.detail || `Image bundle unavailable (${upstream.status})` },
        { status: upstream.status === 404 ? 404 : 502 }
      )
    }
    return new NextResponse(upstream.body, {
      headers: {
        'Content-Type': 'application/zip',
        'Content-Disposition': `attachment; filename="images-${params.id.slice(-8)}.zip"`,
        'Cache-Control': 'no-store',
      },
    })
  } catch (error) {
    console.error(error)
    return NextResponse.json(
      { error: 'The processing service is unreachable; the image bundle cannot be built' },
      { status: 502 }
    )
  }
}
