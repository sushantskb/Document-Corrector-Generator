'use client'

import { forwardRef } from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

type Variant = 'primary' | 'secondary' | 'ghost' | 'success' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const variants: Record<Variant, string> = {
  primary:
    'bg-primary-fill text-white shadow-sm hover:bg-primary-fill-hover active:scale-[0.99] disabled:bg-surface-muted disabled:text-muted disabled:shadow-none disabled:ring-1 disabled:ring-inset disabled:ring-border',
  secondary:
    'border border-border bg-surface text-foreground shadow-sm hover:border-border-strong hover:bg-surface-muted active:scale-[0.99]',
  ghost: 'text-muted hover:bg-surface-muted hover:text-foreground',
  success:
    'bg-success-fill text-white shadow-sm hover:bg-success-fill-hover active:scale-[0.99] disabled:bg-surface-muted disabled:text-muted disabled:shadow-none disabled:ring-1 disabled:ring-inset disabled:ring-border',
  danger:
    'bg-danger text-white shadow-sm hover:brightness-110 active:scale-[0.99] disabled:bg-surface-muted disabled:text-muted disabled:shadow-none disabled:ring-1 disabled:ring-inset disabled:ring-border',
}

const sizes: Record<Size, string> = {
  sm: 'h-9 gap-1.5 px-3 text-sm',
  md: 'h-10 gap-2 px-4 text-sm',
  lg: 'h-11 gap-2 px-5 text-[0.9375rem]',
}

const base =
  'inline-flex select-none items-center justify-center rounded-xl font-medium transition-all duration-150 disabled:pointer-events-none disabled:opacity-70'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, className, children, disabled, ...props },
  ref
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(base, variants[variant], sizes[size], className)}
      {...props}
    >
      {loading && <Loader2 size={16} className="animate-spin" />}
      {children}
    </button>
  )
})

interface ButtonLinkProps extends React.ComponentProps<typeof Link> {
  variant?: Variant
  size?: Size
}

export function ButtonLink({ variant = 'primary', size = 'md', className, ...props }: ButtonLinkProps) {
  return <Link className={cn(base, variants[variant], sizes[size], className)} {...props} />
}
