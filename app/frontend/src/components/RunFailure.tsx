import { Fragment, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Run } from '../types';
import { Alert, Button, humanize, safeUrl } from './ui';

interface AiFailure extends Record<string, unknown> {
  kind: string;
  title: string;
  explanation: string;
  remedy: string;
}

function readFailure(value: unknown): AiFailure | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const fields = value as Record<string, unknown>;
  return ['kind', 'title', 'explanation', 'remedy'].every((key) =>
    typeof fields[key] === 'string' && fields[key].trim()) ? fields as AiFailure : null;
}

function numberField(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function nonEmpty(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.some(nonEmpty);
  if (typeof value === 'object') return Object.values(value).some(nonEmpty);
  return true;
}

function diagnosticText(value: unknown): string {
  return typeof value === 'string' ? value : typeof value === 'number'
    ? value.toLocaleString() : JSON.stringify(value, null, 2);
}

export function RunFailure({ run }: { run: Run }) {
  const failure = readFailure(run.result?.ai_failure);
  const [copyState, setCopyState] = useState('');
  useEffect(() => {
    if (!copyState) return;
    const timer = window.setTimeout(() => setCopyState(''), 2500);
    return () => window.clearTimeout(timer);
  }, [copyState]);
  if (!failure && !run.error) return null;

  const facts: string[] = [];
  if (typeof failure?.stage === 'string' && failure.stage) facts.push(humanize(failure.stage));
  if (typeof failure?.model === 'string' && failure.model) facts.push(failure.model);
  const outputTokens = numberField(failure?.output_tokens);
  const thinkingTokens = numberField(failure?.thinking_tokens);
  const tokenCap = numberField(failure?.output_token_cap);
  if (outputTokens !== undefined) {
    let tokens = `Used ${outputTokens.toLocaleString()} output tokens`;
    if (thinkingTokens !== undefined && outputTokens > 0) {
      tokens += ` (${Math.round(thinkingTokens / outputTokens * 100).toLocaleString()}% thinking)`;
    }
    if (tokenCap !== undefined) tokens += ` of a ${tokenCap.toLocaleString()}-token cap per attempt`;
    facts.push(tokens);
  }
  const httpStatus = numberField(failure?.api_error_status);
  if (httpStatus !== undefined) facts.push(`HTTP ${httpStatus}`);
  const action = failure?.action && typeof failure.action === 'object'
    ? failure.action as Record<string, unknown> : null;
  const actionHref = typeof action?.href === 'string'
    ? (/^\/(?!\/)/.test(action.href) && !action.href.includes('\\') ? action.href : safeUrl(action.href))
    : undefined;
  const fields = Object.entries(failure ?? {}).filter(([key, value]) => key !== 'errors' && nonEmpty(value));
  const errors = Array.isArray(failure?.errors)
    ? failure.errors.filter((value): value is string => typeof value === 'string' && !!value.trim()) : [];

  async function copyDiagnostics() {
    if (!navigator.clipboard?.writeText) {
      setCopyState('Clipboard unavailable. Copy the technical details manually.');
      return;
    }
    try {
      await navigator.clipboard.writeText(JSON.stringify({
        run_id: run.id, kind: run.kind, stage: run.stage, error_code: run.error_code,
        error: run.error, ai_failure: failure,
      }, null, 2));
      setCopyState('Copied');
    } catch {
      setCopyState('Could not copy. Copy the technical details manually.');
    }
  }

  return <Alert tone="danger" title={failure?.title ?? (run.status === 'interrupted'
    ? 'Work was interrupted' : 'The operation did not complete')}>
    {failure && <>
      <h3 className="failure-heading">What happened</h3>
      <p className="preserve-lines">{failure.explanation}</p>
      <h3 className="failure-heading">What to do</h3>
      <p className="preserve-lines">{failure.remedy}</p>
      {actionHref && typeof action?.label === 'string' && action.label && <div className="actions">
        <Link className="button button-secondary" to={actionHref}>{action.label}</Link>
      </div>}
      {facts.length > 0 && <p className="hint failure-facts">{facts.join(' · ')}</p>}
    </>}
    {run.error && <p className="preserve-lines">{run.error}</p>}
    {!failure && run.error_code && <p className="hint">Reference: {run.error_code}</p>}
    <p>Saved drafts are preserved.</p>
    <details><summary>Technical details</summary>
      <dl className="failure-diagnostics">
        {fields.map(([key, value]) => <Fragment key={key}>
          <dt>{humanize(key)}</dt><dd className="preserve-lines">{diagnosticText(value)}</dd>
        </Fragment>)}
        {errors.length > 0 && <><dt>Errors</dt><dd><ul>{errors.map((error, index) =>
          <li className="preserve-lines" key={index}>{error}</li>)}</ul></dd></>}
        {run.error_code && <><dt>Reference</dt><dd>{run.error_code}</dd></>}
        <dt>Run ID</dt><dd>{run.id}</dd>
      </dl>
    </details>
    <div className="actions"><Button variant="secondary" onClick={copyDiagnostics}>Copy diagnostics</Button>
      <span className="hint" role="status">{copyState}</span></div>
  </Alert>;
}
