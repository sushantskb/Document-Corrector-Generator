import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { Breadcrumbs } from '@/components/Navigation'
import { Header } from '@/components/layout/Header'
import { Footer } from '@/components/layout/Footer'
import { ToastViewport } from '@/components/Toast'
import { ToastProvider } from '@/lib/hooks/useToast'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-sans', display: 'swap' })

export const metadata: Metadata = {
  title: 'Document Correction Platform',
  description: 'Verify and correct PDF-to-HTML conversions',
}

// Applies the stored theme before paint so there is no flash of the wrong palette.
const themeScript = `
try {
  var stored = localStorage.getItem('theme')
  var dark = stored ? stored === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches
  if (dark) document.documentElement.classList.add('dark')
} catch (e) {}
`

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen font-sans">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:shadow-lift"
        >
          Skip to content
        </a>

        <ToastProvider>
          <div className="pointer-events-none fixed inset-x-0 top-0 -z-10 h-80 bg-gradient-to-b from-background-accent to-background" />

          <Header />
          <Breadcrumbs />

          <main id="main" className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
            {children}
          </main>

          <Footer />
          <ToastViewport />
        </ToastProvider>
      </body>
    </html>
  )
}
