import { useId, useState } from 'react';
import { useApiQuery } from '../query';
import type { ApiSchema, Run } from '../types';
import { Alert, Button, ErrorPanel, Field, humanize, Loading } from './ui';

function display(value: unknown) {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

export function AIExchangeDebug({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState('');
  const [copyError, setCopyError] = useState<Error | null>(null);
  const [copied, setCopied] = useState(false);
  const id = useId();
  const path = `/runs/${encodeURIComponent(run.id)}/ai-traces`;
  const index = useApiQuery<ApiSchema<'AITraceSummary'>[]>(['ai-traces', run.id], path, { enabled: open });
  const traceId = index.data?.some((item) => item.id === selected) ? selected : index.data?.at(-1)?.id;
  const trace = useApiQuery<ApiSchema<'AITraceDetail'>>(['ai-trace', run.id, traceId],
    `${path}/${encodeURIComponent(traceId ?? '')}`, { enabled: open && !!traceId });
  async function copyExchange() {
    setCopyError(null);
    try {
      await navigator.clipboard.writeText(JSON.stringify(trace.data, null, 2));
      setCopied(true);
    } catch {
      setCopyError(new Error('Could not copy the exchange. Select and copy the text below instead.'));
    }
  }
  return <details className="ai-exchange-debug" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>AI prompts & responses (debug)</summary>
    {open && <div className="stack">
      <Alert tone="warning">Contains personal profile, posting and document text. Stored in this local workspace
        and included in workspace backups. Credentials are redacted; do not share without reviewing.</Alert>
      <p className="hint">Shows application-supplied prompts, requested output schema, public replies and tool results.
        Private reasoning and SDK-added instructions are not shown. Opening this view does not call AI.</p>
      <Button variant="secondary" busy={index.isFetching || trace.isFetching} onClick={() => {
        void index.refetch(); if (traceId) void trace.refetch();
      }}>Refresh exchanges</Button>
      <ErrorPanel error={index.error} retry={() => void index.refetch()} />
      {index.isPending && <Loading label="Loading recorded AI stages..." />}
      {index.data?.length === 0 && <p>No recorded AI exchanges for this operation. Local-only stages and reused
        checkpoints do not send a new prompt. Open the original operation for reused results.</p>}
      {!!index.data?.length && <Field id={id} label="AI stage">
        <select id={id} value={traceId ?? ''} onChange={(event) => {
          setSelected(event.target.value); setCopied(false); setCopyError(null);
        }}>
          {index.data.map((item, position) => <option key={item.id} value={item.id}>
            {position + 1}. {humanize(item.stage)} · {item.source === 'captured' ? 'Recorded exchange' : 'Legacy snapshot'}
          </option>)}
        </select>
      </Field>}
      <ErrorPanel error={trace.error} retry={() => void trace.refetch()} />
      {traceId && trace.isPending && <Loading label="Loading AI prompt and response..." />}
      {trace.data && <>
        <p className="hint">{trace.data.note}</p>
        <p className="hint">Stage: {trace.data.stage} · {humanize(trace.data.status)} · {trace.data.created_at}</p>
        <Button variant="secondary" onClick={() => void copyExchange()}>{copied ? 'Exchange copied' : 'Copy exchange'}</Button>
        <ErrorPanel error={copyError} />
        <h3>Sent to AI</h3>
        {Object.entries(trace.data.request).map(([key, value]) => <details key={key}>
          <summary>{humanize(key)}</summary><pre className="plain-text ai-debug-text">{display(value)}</pre>
        </details>)}
        <h3>Received from AI</h3>
        {trace.data.responses.length ? <pre className="plain-text ai-debug-text">{display(trace.data.responses)}</pre>
          : <p>No response was recorded. The request may still be running, interrupted, or from an older run.</p>}
      </>}
    </div>}
  </details>;
}
