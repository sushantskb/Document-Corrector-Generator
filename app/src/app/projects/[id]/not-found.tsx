import { FolderX } from 'lucide-react'
import { ButtonLink } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'

export default function ProjectNotFound() {
  return (
    <EmptyState
      icon={FolderX}
      title="Project not found"
      description="This project may have been deleted, or the link is out of date."
      action={<ButtonLink href="/">Back to projects</ButtonLink>}
      className="mx-auto max-w-lg"
    />
  )
}
