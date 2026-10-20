import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, artifactUrl } from '../api';
import { useAction, useNotices } from '../query';
import type { ApiSchema, DocumentBlock, DocumentContent, DocumentKind, DocumentView, Evidence } from '../types';
import { Alert, Badge, Button, ErrorPanel, Field, Modal } from './ui';
import { PdfPreview, ReadDocument } from './PdfPreview';

function clone(content: DocumentContent): DocumentContent {
  return structuredClone(content);
}

function EvidenceLinks({ ids, evidence, onChange }: {
  ids: string[]; evidence: Evidence[]; onChange: (ids: string[]) => void;
}) {
  return <details className="evidence-links"><summary>Supporting evidence ({ids.length})</summary>
    {ids.length ? <ul>{ids.map((id) => {
      const fact = evidence.find((item) => item.id === id);
      return <li key={id}>{fact ? <><p>{fact.text}</p><span className="hint">{fact.source} · {fact.section}</span></>
        : <p className="field-error">Evidence {id} is not in the current profile. Review this claim before using it.</p>}</li>;
    })}</ul> : <p className="hint">No evidence linked. Factual claims should be supported by confirmed profile facts.</p>}
    {evidence.length > 0 && <fieldset className="evidence-picker"><legend>Link confirmed facts</legend>
      {evidence.map((fact) => <label className="checkbox-row" key={fact.id}>
        <input type="checkbox" checked={ids.includes(fact.id)} disabled={ids.length >= 30 && !ids.includes(fact.id)}
          onChange={() => onChange(ids.includes(fact.id) ? ids.filter((id) => id !== fact.id) : [...ids, fact.id])} />
        <span>{fact.text}<small>{fact.section} · {fact.source}</small></span></label>)}
    </fieldset>}
    <p><Link to="/profile">Correct or add a fact in your profile →</Link></p>
  </details>;
}

export function isVerifiedDocument(view: DocumentView) {
  return !view.stale && view.checks.length > 0 && view.checks.every((check) => check.status === 'pass');
}

export function DocumentChecks({ view, stale }: { view: DocumentView; stale: boolean }) {
  return <section aria-label="Document verification" className="document-checks">
    <h3>Verification & keyword coverage</h3>
    {stale && <p className="field-error">Checks are out of date until the current edits are compiled and verified.</p>}
    {view.checks.length ? <ul className="checks-list">{view.checks.map((check) => <li key={check.id}>
      <Badge value={stale ? 'pending' : check.status} /><div><strong>{check.label}</strong>
        <p className="hint">{stale ? 'This result must be checked again for the current content.' : check.message}</p></div>
    </li>)}</ul> : <p className="hint">No verification results yet. A missing or skipped check is not a pass.</p>}
    {!!view.keyword_coverage?.length && <details><summary>Posting keywords</summary>
      <div className="table-scroll"><table><caption className="sr-only">Posting keyword coverage</caption><thead><tr>
        <th>Term</th><th>Priority</th><th>Coverage</th><th>Note</th></tr></thead><tbody>
        {view.keyword_coverage.map((item, index) => <tr key={`${item.term}-${index}`}><td>{item.term}</td><td>{item.priority}</td>
          <td>{stale ? 'Needs checking' : item.status}</td><td>{stale ? 'Recheck after compilation.' : item.note}</td></tr>)}
      </tbody></table></div><p className="hint">Genuine gaps stay visible. Do not add skills or keywords you cannot substantiate.</p>
    </details>}
  </section>;
}

export function DocumentEditor({ applicationId, kind, view, evidence, onDirtyChange, initialPane = 'content', focusBlockId, editRequest }: {
  applicationId: string; kind: DocumentKind; view: DocumentView; evidence: Evidence[];
  onDirtyChange: (dirty: boolean) => void;
  initialPane?: 'content' | 'read';
  focusBlockId?: string;
  editRequest?: number;
}) {
  const [content, setContent] = useState(() => clone(view.content));
  const [savedContent, setSavedContent] = useState(() => clone(view.content));
  const [revision, setRevision] = useState(view.revision);
  const [history, setHistory] = useState<DocumentContent[]>([]);
  const [discard, setDiscard] = useState(false);
  const [pane, setPane] = useState<'read' | 'content' | 'preview'>(initialPane);
  const [comparison, setComparison] = useState<{ before: DocumentContent; after: DocumentContent }>();
  const dirty = JSON.stringify(content) !== JSON.stringify(savedContent);
  const client = useQueryClient();
  const { notify } = useNotices();
  const path = `/applications/${encodeURIComponent(applicationId)}/documents/${kind}`;
  const save = useAction(async (snapshot: DocumentContent) => {
    await api<unknown>(path, { method: 'PATCH', body: { content: snapshot, expected_revision: revision } satisfies ApiSchema<'DocumentEdit'> });
    return api<DocumentView>(path);
  }, (saved) => {
    setContent(clone(saved.content)); setSavedContent(clone(saved.content)); setRevision(saved.revision);
    client.setQueryData(['document', applicationId, kind], { ...saved, stale: true });
    notify('Document edits saved. Compile and check them before using a PDF.');
  });
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (view.revision !== revision && !dirty) {
      if (JSON.stringify(savedContent) !== JSON.stringify(view.content)) {
        setComparison({ before: clone(savedContent), after: clone(view.content) });
      }
      setContent(clone(view.content)); setSavedContent(clone(view.content));
      setRevision(view.revision); setHistory([]);
    }
  }, [view, revision, dirty, savedContent]);
  useEffect(() => {
    if (editRequest) setPane('content');
  }, [editRequest]);
  useEffect(() => {
    if (!focusBlockId || pane !== 'content') return;
    document.getElementById(`block-title-${focusBlockId}`)?.scrollIntoView?.({ block: 'center' });
    document.getElementById(`block-text-${focusBlockId}`)?.focus();
  }, [focusBlockId, pane, editRequest]);

  function update(change: (draft: DocumentContent) => void) {
    setHistory((previous) => [...previous.slice(-49), clone(content)]);
    const next = clone(content); change(next); setContent(next);
  }
  function blockUpdate(index: number, values: Partial<DocumentBlock>) {
    update((draft) => { draft.blocks[index] = { ...draft.blocks[index], ...values }; });
  }
  function move(index: number, direction: number) {
    update((draft) => {
      const [block] = draft.blocks.splice(index, 1); draft.blocks.splice(index + direction, 0, block);
    });
  }
  function reload() {
    setContent(clone(view.content)); setSavedContent(clone(view.content)); setRevision(view.revision);
    setHistory([]); setDiscard(false); save.reset();
  }
  const stale = dirty || view.stale || save.isPending || view.revision !== revision;
  const newer = view.revision !== revision;
  return <div className="stack">
    <div className="editor-toolbar"><div className="actions"><Badge value={dirty ? 'pending' : 'ready'}>
      {dirty ? 'Unsaved edits' : 'Content saved'}</Badge><span className="hint">PDF and checks are separate from saving.</span></div>
      <div className="actions"><Button variant="secondary" disabled={!history.length || save.isPending} onClick={() => {
        const previous = history.at(-1); if (previous) { setContent(previous); setHistory((items) => items.slice(0, -1)); }
      }}>Undo last edit</Button>
        <Button variant="ghost" disabled={(!dirty && !newer) || save.isPending} onClick={() => setDiscard(true)}>Reload saved content</Button>
        <Button busy={save.isPending} disabled={!dirty || newer} onClick={() => save.mutate(clone(content))}>Save document edits</Button></div>
    </div>
    <ErrorPanel error={save.error} />
    {newer && dirty && <Alert tone="warning" title="A newer document revision is available">
      Your unsaved edits have been preserved, but cannot overwrite the newer revision.
      Compare the content below and reload the saved version before making changes.
      <details><summary>Newer saved content</summary><ReadDocument content={view.content} /></details>
    </Alert>}
    <div className="mobile-pane-switch" role="group" aria-label="Document workspace view">
      <Button variant={pane === 'read' ? 'primary' : 'secondary'} aria-pressed={pane === 'read'} onClick={() => setPane('read')}>Read draft</Button>
      <Button variant={pane === 'content' ? 'primary' : 'secondary'} aria-pressed={pane === 'content'} onClick={() => setPane('content')}>Edit content</Button>
      <Button variant={pane === 'preview' ? 'primary' : 'secondary'} aria-pressed={pane === 'preview'} onClick={() => setPane('preview')}>PDF preview</Button>
    </div>
    {pane === 'read' && <section aria-label="Readable saved draft" className="stack">
      <div className="actions"><Badge value={!stale && isVerifiedDocument(view) ? 'verified' : 'unverified'} />
        <span className="hint">Readable text is available before PDF compilation. A draft is never a submission.</span></div>
      <ReadDocument content={content} />
    </section>}
    <div className={`document-split show-${pane}`} hidden={pane === 'read'}>
      <section className="document-content-panel" aria-label="Editable document content">
        <fieldset className="editor-fieldset" disabled={save.isPending}>
          <legend className="sr-only">Document content blocks</legend>
          <Field id="document-language" label="Document language"><input id="document-language" maxLength={500} value={content.language}
            onChange={(event) => update((draft) => { draft.language = event.target.value; })} /></Field>
          <div className="stack">{content.blocks.map((block, index) => <section className="document-block" key={block.id}
            aria-labelledby={`block-title-${block.id}`}>
            <div className="block-toolbar"><h3 id={`block-title-${block.id}`}>Block {index + 1}</h3>
              <div className="actions"><Button variant="ghost" disabled={index === 0} aria-label={`Move block ${index + 1} up`} onClick={() => move(index, -1)}>↑</Button>
                <Button variant="ghost" disabled={index === content.blocks.length - 1} aria-label={`Move block ${index + 1} down`} onClick={() => move(index, 1)}>↓</Button>
                <Button variant="ghost" aria-label={`Remove block ${index + 1}`} onClick={() => update((draft) => { draft.blocks.splice(index, 1); })}>Remove</Button></div></div>
            <Field id={`block-kind-${block.id}`} label="Block type"><select id={`block-kind-${block.id}`} value={block.kind}
              onChange={(event) => blockUpdate(index, { kind: event.target.value as DocumentBlock['kind'] })}>
              <option value="heading">Heading</option><option value="paragraph">Paragraph</option><option value="bullet">Bullet</option><option value="entry">Experience / education entry</option>
            </select></Field>
            {(block.kind === 'heading' || block.kind === 'entry' || block.heading) && <Field id={`block-heading-${block.id}`} label="Heading or title">
              <input id={`block-heading-${block.id}`} maxLength={500} value={block.heading} onChange={(event) => blockUpdate(index, { heading: event.target.value })} /></Field>}
            {block.kind === 'entry' && <div className="form-grid">
              <Field id={`block-org-${block.id}`} label="Organization"><input id={`block-org-${block.id}`} maxLength={500} value={block.organization} onChange={(event) => blockUpdate(index, { organization: event.target.value })} /></Field>
              <Field id={`block-location-${block.id}`} label="Location"><input id={`block-location-${block.id}`} maxLength={500} value={block.location} onChange={(event) => blockUpdate(index, { location: event.target.value })} /></Field>
              <Field id={`block-start-${block.id}`} label="Start"><input id={`block-start-${block.id}`} maxLength={500} value={block.start} onChange={(event) => blockUpdate(index, { start: event.target.value })} /></Field>
              <Field id={`block-end-${block.id}`} label="End"><input id={`block-end-${block.id}`} maxLength={500} value={block.end} onChange={(event) => blockUpdate(index, { end: event.target.value })} /></Field>
            </div>}
            <Field id={`block-text-${block.id}`} label={block.kind === 'bullet' ? 'Bullet text' : 'Body text'}>
              <textarea id={`block-text-${block.id}`} maxLength={20_000} rows={block.kind === 'heading' ? 2 : 5} value={block.text}
                onChange={(event) => blockUpdate(index, { text: event.target.value })} /></Field>
            <EvidenceLinks ids={block.evidence_ids} evidence={evidence}
              onChange={(ids) => blockUpdate(index, { evidence_ids: ids })} />
            {block.bullets.map((bullet, bulletIndex) => <div className="claim-block" key={bullet.id}>
              <Field id={`claim-${bullet.id}`} label={`Supporting bullet ${bulletIndex + 1}`}>
                <textarea id={`claim-${bullet.id}`} value={bullet.text} maxLength={20_000} rows={3} onChange={(event) =>
                  update((draft) => { draft.blocks[index].bullets[bulletIndex].text = event.target.value; })} /></Field>
              <EvidenceLinks ids={bullet.evidence_ids} evidence={evidence} onChange={(ids) =>
                update((draft) => { draft.blocks[index].bullets[bulletIndex].evidence_ids = ids; })} />
              <Button variant="ghost" onClick={() => update((draft) => { draft.blocks[index].bullets.splice(bulletIndex, 1); })}>Remove supporting bullet {bulletIndex + 1}</Button>
            </div>)}
            <Button variant="secondary" disabled={block.bullets.length >= 30} onClick={() => update((draft) => {
              draft.blocks[index].bullets.push({ id: crypto.randomUUID(), text: '', evidence_ids: [] });
            })}>+ Add supporting bullet</Button>
          </section>)}</div>
          <Button variant="secondary" disabled={content.blocks.length >= 100} onClick={() => update((draft) => draft.blocks.push({
            id: crypto.randomUUID(), kind: 'paragraph', text: '', heading: '', organization: '', location: '',
            start: '', end: '', evidence_ids: [], bullets: [],
          }))}>+ Add content block</Button>
        </fieldset>
      </section>
      {pane !== 'read' && <section className="document-preview-panel" aria-label="Compiled PDF preview">
        <h3>PDF preview</h3><PdfPreview artifactId={view.pdf_artifact_id} stale={stale} content={content} />
        {view.source_artifact_id && <p><a href={artifactUrl(view.source_artifact_id)} download>Download generated source</a> <span className="hint">(advanced)</span></p>}
        <DocumentChecks view={view} stale={stale} />
      </section>}
    </div>
    {(dirty || comparison) && <details><summary>{dirty ? 'Compare current edits with saved content' : 'Compare the previous and current document'}</summary>
      <div className="comparison"><section><h3>Before</h3><ReadDocument content={dirty ? savedContent : comparison!.before} /></section>
        <section><h3>After</h3><ReadDocument content={dirty ? content : comparison!.after} /></section></div>
    </details>}
    <Modal open={discard} title="Reload saved document content?" onClose={() => setDiscard(false)}>
      <p>Unsaved edits will be discarded. Compiled files and the submitted archive are not changed.</p>
      <div className="actions"><Button variant="secondary" onClick={() => setDiscard(false)}>Keep editing</Button>
        <Button onClick={reload}>Reload saved content</Button></div>
    </Modal>
  </div>;
}
