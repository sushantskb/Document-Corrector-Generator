import { SearchX } from 'lucide-react'
import { ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'

export default function JobNotFound() {
  return (
    <EmptyState
      icon={SearchX}
      title="Job not found"
      description="This processing run no longer exists."
      action={<ButtonLink href="/">Back to projects</ButtonLink>}
      className="mx-auto max-w-lg"
    />
  )
}
