import { BookOpen, Github } from 'lucide-react'

export function Footer() {
  return (
    <footer className="mx-auto max-w-7xl px-4 pb-10 sm:px-6 lg:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-6 text-xs text-muted">
        <p>Document Correction Platform · Phase 1 · v0.1.0</p>
        <div className="flex items-center gap-4">
          <a href="/README.md" className="inline-flex items-center gap-1.5 transition hover:text-foreground">
            <BookOpen size={13} /> Documentation
          </a>
          <a
            href="https://github.com"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 transition hover:text-foreground"
          >
            <Github size={13} /> Source
          </a>
        </div>
      </div>
    </footer>
  )
}
