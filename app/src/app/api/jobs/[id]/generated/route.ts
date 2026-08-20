import { NextRequest, NextResponse } from 'next/server'
import { connectDB } from '@/lib/mongodb'
import { applyCdnImageUrls } from '@/lib/cdn'
import { Job } from '@/lib/models'

/**
 * Streams the HTML the processing service generated straight from the PDF.
 *
 * This is the add-on rendition: when the uploaded HTML is an enriched study
 * guide rather than a mirror of the PDF, patching it has a ceiling — this file
 * is the PDF's own content, cleanly laid out.
 */
export async function GET(request: NextRequest, { params }: { params: { id: string } }) {
  try {
    await connectDB()
    const job = await Job.findById(params.id)

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }
    if (!job.generatedHtmlUrl) {
      return NextResponse.json(
        { error: 'This job has no generated document — reprocess it to create one' },
        { status: 404 }
      )
    }

    const upstream = await fetch(job.generatedHtmlUrl, { signal: AbortSignal.timeout(30000) })
    if (!upstream.ok) {
      return NextResponse.json(
        { error: `Could not fetch the generated document (${upstream.status})` },
        { status: 502 }
      )
    }

    const html = await upstream.text()
    const inline = request.nextUrl.searchParams.get('inline') === '1'
    const filename = `generated-${String(job._id).slice(-8)}.html`

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
    return NextResponse.json({ error: 'Failed to load the generated document' }, { status: 500 })
  }
}
