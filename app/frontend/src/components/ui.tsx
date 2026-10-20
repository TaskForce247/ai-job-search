import { useEffect, useId, useRef } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api';

export function Button({
  children, busy = false, variant = 'primary', className = '', disabled, ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  busy?: boolean;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
}) {
  return <button {...props} className={`button button-${variant} ${className}`}
    disabled={disabled || busy} aria-busy={busy || undefined}>
    {busy && <span className="spinner" aria-hidden="true" />}{children}
  </button>;
}

export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow?: string; title: string; description?: string; actions?: ReactNode;
}) {
  return <header className="page-header">
    <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>
      {description && <p className="page-description">{description}</p>}</div>
    {actions && <div className="actions">{actions}</div>}
  </header>;
}

export function Panel({ title, children, className = '', actions }: {
  title?: string; children: ReactNode; className?: string; actions?: ReactNode;
}) {
  return <section className={`panel ${className}`}>
    {title && <div className="section-heading"><h2>{title}</h2>{actions}</div>}{children}
  </section>;
}

export function Field({ label, hint, error, children, id, className = '' }: {
  label: string; hint?: string; error?: string; children: ReactNode; id: string; className?: string;
}) {
  return <div className={`field ${className}`}>
    <label htmlFor={id}>{label}</label>{hint && <p id={`${id}-hint`} className="hint">{hint}</p>}
    {children}
    {error && <p id={`${id}-error`} className="field-error" role="alert">{error}</p>}
  </div>;
}

export function Alert({ children, tone = 'info', title }: {
  children: ReactNode; tone?: 'info' | 'warning' | 'danger' | 'success'; title?: string;
}) {
  return <div className={`alert alert-${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
    {title && <strong>{title}</strong>}<div>{children}</div>
  </div>;
}

export function ErrorPanel({ error, retry }: { error: unknown; retry?: () => void }) {
  if (!error) return null;
  const apiError = error instanceof ApiError ? error : undefined;
  const message = error instanceof Error ? error.message : 'Something went wrong. Please try again.';
  const revisionConflict = apiError?.status === 409 &&
    /revision|external_edit|stale|source_changed|input_changed|approval_invalidated/i.test(apiError.code);
  return <Alert tone="danger" title="This action needs attention">
    <p>{message}</p>
    {revisionConflict && <p>Your files changed since this screen was opened.
      Refresh the latest version and review it before saving again. Your edits have not been overwritten.</p>}
    {apiError?.status === 403 && <p>Refresh this page. If your session expired, use a fresh login link from the backend terminal.</p>}
    {apiError?.detail != null && <details><summary>Technical details</summary>
      <pre className="plain-text">{typeof apiError.detail === 'string'
        ? apiError.detail : JSON.stringify(apiError.detail, null, 2)}</pre></details>}
    {apiError && <p className="hint">Reference: {apiError.code}</p>}
    {retry && <Button variant="secondary" onClick={retry}>Try again</Button>}
  </Alert>;
}

export function Loading({ label = 'Loading your workspace…' }: { label?: string }) {
  return <div className="loading-state" role="status"><span className="spinner" aria-hidden="true" />{label}</div>;
}

export function Empty({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-symbol" aria-hidden="true">↗</div>
    <h2>{title}</h2><p>{children}</p>{action && <div className="actions">{action}</div>}</div>;
}

const labels: Record<string, string> = {
  preparing: 'Preparing · not yet verified', drafted: 'Drafted · not sent', applied: 'Applied', submitted: 'Submitted', interview: 'Interview',
  offer: 'Offer', hired: 'Hired', rejected: 'Rejected', no_response: 'No response',
  offer_declined: 'Offer declined', withdrawn: 'Withdrawn', awaiting_user: 'Your decision needed',
  queued: 'Queued', running: 'In progress', succeeded: 'Completed', failed: 'Failed',
  cancelled: 'Cancelled', interrupted: 'Interrupted', invalidated: 'Out of date',
  setup_required: 'Setup required', unavailable: 'Unavailable', ready: 'Ready', optional: 'Optional',
  pass: 'Passed', fail: 'Failed', skipped: 'Not checked', pending: 'Needs checking',
  approved: 'Approved', denied: 'Not approved', rejected_approval: 'Rejected',
  needs_review: 'Needs review', unverified: 'Unverified draft', verified: 'Verified draft',
};

export function humanize(value: string) {
  return labels[value] ?? value.replace(/[_-]/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

export function Badge({ value, children }: { value: string; children?: ReactNode }) {
  let tone = 'neutral';
  if (['ready', 'pass', 'PASS', 'succeeded', 'approved', 'hired', 'offer', 'verified'].includes(value)) tone = 'positive';
  if (['failed', 'fail', 'FAIL', 'rejected', 'unavailable'].includes(value)) tone = 'negative';
  if (['awaiting_user', 'pending', 'FLAG', 'setup_required', 'interrupted', 'drafted', 'needs_review', 'unverified'].includes(value)) tone = 'amber';
  if (['preparing', 'running', 'queued', 'applied', 'interview'].includes(value)) tone = 'blue';
  return <span className={`badge badge-${tone}`}>{children ?? humanize(value)}</span>;
}

export function formatDate(value: string | null | undefined, withTime = false) {
  if (!value) return 'Not known';
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium', ...(withTime ? { timeStyle: 'short' as const } : {}),
  }).format(date);
}

export function today() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}

export function ExternalLink({ href, children, className = '' }: {
  href: string | null | undefined; children: ReactNode; className?: string;
}) {
  const safe = safeUrl(href);
  return safe ? <a className={className} href={safe} target="_blank" rel="noopener noreferrer">
    {children}<span className="external-mark" aria-hidden="true"> ↗</span>
    <span className="sr-only"> (opens in a new tab)</span>
  </a> : null;
}

export function Modal({ open, title, children, onClose }: {
  open: boolean; title: string; children: ReactNode; onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} aria-labelledby={headingId} onCancel={(event) => {
    event.preventDefault(); onClose();
  }} onClick={(event) => { if (event.target === ref.current) onClose(); }}>
    <div className="dialog-content">
      <div className="section-heading"><h2 id={headingId}>{title}</h2>
        <Button variant="ghost" onClick={onClose} aria-label="Close dialog">×</Button></div>
      {children}
    </div>
  </dialog>;
}

export function Pager({ next, previous, hasNext, hasPrevious, total, count }: {
  next: () => void; previous: () => void; hasNext: boolean; hasPrevious: boolean;
  total: number; count: number;
}) {
  return <nav aria-label="Results pages" className="pagination">
    <p className="hint">Showing {count} of {total} records</p>
    <div className="actions"><Button variant="secondary" disabled={!hasPrevious} onClick={previous}>Previous</Button>
      <Button variant="secondary" disabled={!hasNext} onClick={next}>Next</Button></div>
  </nav>;
}

export function ApprovalLink({ runId }: { runId: string }) {
  return <Link to={`/activity#run-${encodeURIComponent(runId)}`}>Follow progress in Activity →</Link>;
}
