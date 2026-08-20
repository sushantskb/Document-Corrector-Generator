/** Talking to the Python processing service. */

const PROCESSOR_URL = process.env.PYTHON_PROCESSOR_URL || 'http://localhost:8000'

export type RebuildOutcome = 'started' | 'coalesced' | 'unavailable'

/**
 * Ask the processing service to rebuild a job's corrected document.
 *
 * The reviewer's decision is already saved by the time this runs, so it is
 * best-effort: if the service is down the review still stands and the document
 * can be rebuilt later. The service coalesces requests, so a burst of decisions
 * does not start a rebuild per decision.
 */
export async function requestRebuild(jobId: string): Promise<RebuildOutcome> {
  try {
    const response = await fetch(`${PROCESSOR_URL}/jobs/${jobId}/rebuild`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(5000),
    })
    if (!response.ok) return 'unavailable'
    const body = await response.json()
    return body?.rebuild === 'coalesced' ? 'coalesced' : 'started'
  } catch (error) {
    console.warn('Corrected document rebuild could not be requested:', error)
    return 'unavailable'
  }
}
