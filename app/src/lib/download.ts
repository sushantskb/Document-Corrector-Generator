function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

const escapeCell = (value: unknown) => {
  const text = String(value ?? '')
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function downloadCsv(filename: string, headers: string[], rows: Array<Array<unknown>>) {
  const csv = [headers, ...rows].map((row) => row.map(escapeCell).join(',')).join('\n')
  triggerDownload(new Blob([csv], { type: 'text/csv;charset=utf-8' }), filename)
}

export function downloadText(filename: string, contents: string, mime = 'text/plain;charset=utf-8') {
  triggerDownload(new Blob([contents], { type: mime }), filename)
}
