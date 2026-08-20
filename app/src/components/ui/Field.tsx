import { cn } from '@/lib/utils'

export function Field({
  label,
  hint,
  htmlFor,
  className,
  children,
}: {
  label: string
  hint?: string
  htmlFor?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-foreground">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  )
}

export function SegmentedControl<T extends string | number>({
  value,
  options,
  onChange,
  name,
}: {
  value: T
  options: Array<{ value: T; label: string }>
  onChange: (value: T) => void
  name: string
}) {
  return (
    <div role="radiogroup" aria-label={name} className="flex flex-wrap gap-1.5 rounded-xl bg-surface-muted p-1.5">
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={String(option.value)}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex-1 rounded-lg px-3 py-1.5 text-sm font-medium transition-all duration-150',
              active
                ? 'bg-surface text-foreground shadow-sm ring-1 ring-border'
                : 'text-muted hover:text-foreground'
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
