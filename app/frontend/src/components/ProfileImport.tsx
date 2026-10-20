import { useRef, useState } from 'react';
import { api } from '../api';
import { useAction, useNotices } from '../query';
import type { RunAccepted } from '../types';
import { Alert, ApprovalLink, Button, ErrorPanel, Field } from './ui';

export const MAX_DOCUMENT_BYTES = 20 * 1024 * 1024;

export function validateProfileFile(file?: File) {
  if (!file) return 'Choose a PDF, Markdown, or plain-text file.';
  if (file.size > MAX_DOCUMENT_BYTES) return 'This file is larger than 20 MiB. Choose a smaller document.';
  if (!/\.(pdf|md|txt)$/i.test(file.name)) return 'Use a text-readable PDF, .md, or .txt file. DOCX and images are not supported yet.';
  if (file.size === 0) return 'This file is empty. Choose a document containing your profile.';
  return undefined;
}

export function ProfileImport() {
  const input = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string>();
  const { queued } = useNotices();
  const action = useAction((file: File) => {
    const body = new FormData(); body.append('file', file);
    return api<RunAccepted>('/documents/imports', { method: 'POST', body });
  }, (run) => {
    queued(run, 'Document import queued. Extracted facts need your confirmation in Activity.');
    if (input.current) input.current.value = '';
  });
  return <form className="stack" onSubmit={(event) => {
    event.preventDefault();
    const file = input.current?.files?.[0];
    const problem = validateProfileFile(file);
    setError(problem);
    if (!problem && file) action.mutate(file);
  }}>
    <p>Start with an existing CV or career notes. Nothing is added to your profile until you confirm the extracted facts.</p>
    <Field id="profile-document" label="Profile document" error={error}
      hint="PDF, Markdown (.md), or text (.txt). Up to 20 MiB; PDFs up to 50 pages. Text-readable PDFs only.">
      <input id="profile-document" ref={input} type="file"
        accept=".pdf,.md,.txt,application/pdf,text/plain,text/markdown"
        aria-invalid={!!error} aria-describedby="profile-document-hint"
        onChange={() => { setError(undefined); action.reset(); }} />
    </Field>
    <Alert tone="info">Image-only PDF? Paste its text into a .txt file or enter your details manually.
      Extracted content may be sent to your configured AI provider, subject to your data-sharing consent.</Alert>
    <ErrorPanel error={action.error} />
    <div><Button busy={action.isPending} type="submit">Import & review facts</Button></div>
    {action.data && <ApprovalLink runId={action.data.run_id} />}
  </form>;
}
