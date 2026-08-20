/** Rewriting deliverables to the publisher's CDN image convention. */

export type ImageMapEntry = { name: string; src: string; cdnUrl?: string }

/**
 * Point every added figure at its delivery URL.
 *
 * The stored HTML keeps the hosted (Cloudinary) sources so in-app previews
 * render; the file the reviewer downloads must reference the CDN names
 * (kerla_new_NN.png) required by the delivery instructions. Sources are
 * content-addressed, so plain string replacement is unambiguous.
 */
export function applyCdnImageUrls(
  html: string,
  imageMap?: ImageMapEntry[] | null,
  imageUrlBase?: string | null
): string {
  if (!imageMap?.length) return html
  let out = html
  for (const entry of imageMap) {
    const target = entry.cdnUrl || (imageUrlBase ? imageUrlBase + entry.name : null)
    if (!entry.src || !target) continue
    out = out.split(entry.src).join(target)
  }
  return out
}
