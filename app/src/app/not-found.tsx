import { Compass } from 'lucide-react'
import { ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'

export default function NotFound() {
  return (
    <EmptyState
      icon={Compass}
      title="Page not found"
      description="That URL doesn't match anything in this workspace."
      action={<ButtonLink href="/">Back to projects</ButtonLink>}
      className="mx-auto max-w-lg"
    />
  )
}
