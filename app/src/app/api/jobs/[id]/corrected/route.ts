import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { applyCdnImageUrls } from '@/lib/cdn'
import { Job } from '@/lib/models'

/**
 * Streams the corrected HTML the processing service published.
 *
 * The document lives in Cloudinary, so it is fetched here rather than linked
 * directly from the browser: that keeps the download working regardless of
 * cross-origin rules, and lets us send a sensible filename.
 */
export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const job = await Job.findById(params.id)

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }
    if (!job.correctedHtmlUrl) {
      return NextResponse.json(
        { error: 'This job has no corrected document yet' },
        { status: 404 }
      )
    }

    const upstream = await fetch(job.correctedHtmlUrl, { signal: AbortSignal.timeout(30000) })
    if (!upstream.ok) {
      return NextResponse.json(
        { error: `Could not fetch the corrected document (${upstream.status})` },
        { status: 502 }
      )
    }

    const html = await upstream.text()
    const inline = request.nextUrl.searchParams.get('inline') === '1'
    const filename = `corrected-${String(job._id).slice(-8)}.html`

    // The downloaded deliverable must reference images by their CDN names;
    // the inline preview keeps the hosted sources so images render now.
    const output = inline ? html : applyCdnImageUrls(html, job.imageMap, job.imageUrlBase)

    return new NextResponse(output, {
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'Content-Disposition': `${inline ? 'inline' : 'attachment'}; filename="${filename}"`,
        'Cache-Control': 'no-store',
      },
    })
  } catch (error) {
    console.error(error)
    return NextResponse.json({ error: 'Failed to load the corrected document' }, { status: 500 })
  }
}
