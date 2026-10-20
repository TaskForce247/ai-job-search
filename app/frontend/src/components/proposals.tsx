import { Fragment, useState } from 'react';
import type { DocumentContent, DocumentKind } from '../types';
import { ReadDocument } from './PdfPreview';
import { Alert, Badge, ExternalLink, formatDate, humanize } from './ui';

export function asObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not provided';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'object') {
    const object = asObject(value);
    if ('label' in object) return String(object.label);
    if ('verdict' in object) return String(object.verdict);
    return Object.entries(object).filter(([, item]) => typeof item !== 'object')
      .map(([key, item]) => `${humanize(key)}: ${String(item)}`).join(' · ') || 'Details available below';
  }
  return String(value);
}

export function StructuredDetails({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined || value === '') return <span className="hint">Not provided</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="hint">None</span>;
    return <ul className="structured-list">{value.map((item, index) =>
      <li key={index}><StructuredDetails value={item} depth={depth + 1} /></li>)}</ul>;
  }
  if (typeof value === 'object') {
    const entries = Object.entries(asObject(value));
    if (!entries.length) return <span className="hint">None</span>;
    return <dl className={`structured-details ${depth > 1 ? 'structured-nested' : ''}`}>
      {entries.map(([key, item]) => <Fragment key={key}>
        <dt>{humanize(key)}</dt><dd><StructuredDetails value={item} depth={depth + 1} /></dd>
      </Fragment>)}
    </dl>;
  }
  return <span className="preserve-lines">{displayValue(value)}</span>;
}

function getEvaluation(proposal: Record<string, unknown>) {
  const nested = asObject(proposal.evaluation);
  return Object.keys(nested).length ? nested : proposal;
}

export function hasBlockingGate(proposal: Record<string, unknown>) {
  const evaluation = getEvaluation(proposal);
  if (proposal.blocked === true || evaluation.blocked === true) return true;
  return ['language', 'eligibility', 'location', 'language_gate', 'eligibility_gate', 'location_verdict']
    .some((name) => {
      const gate = evaluation[name] ?? proposal[name];
      const verdict = typeof gate === 'string' ? gate : asObject(gate).verdict;
      return typeof verdict === 'string' && ['FAIL', 'BLOCKED'].includes(verdict.toUpperCase());
    });
}

export function GateView({ name, value, note }: { name: string; value: unknown; note?: string | null }) {
  if (value === undefined || value === null || value === '') return null;
  const object = asObject(value);
  const verdict = typeof value === 'string' ? value : displayValue(object.verdict);
  const explanation = note || (typeof object.note === 'string' ? object.note : '');
  return <div className="gate"><div><strong>{name}</strong> <Badge value={verdict} /></div>
    {explanation && <p className="preserve-lines">{explanation}</p>}</div>;
}

export function StrengthsAndGaps({ strengths, gaps }: { strengths: unknown; gaps: unknown }) {
  const strengthList = Array.isArray(strengths) ? strengths : [];
  const gapList = Array.isArray(gaps) ? gaps : [];
  return <div className="form-grid">
    <div><h3>Strengths</h3>{strengthList.length
      ? <ul className="check-list">{strengthList.map((item, i) => <li key={i}>{displayValue(item)}</li>)}</ul>
      : <p className="hint">No strengths reported yet.</p>}</div>
    <div><h3>Gaps & uncertainties</h3>{gapList.length
      ? <ul>{gapList.map((item, i) => <li key={i}>{displayValue(item)}</li>)}</ul>
      : <p className="hint">No gaps reported. This is not a guarantee of eligibility.</p>}</div>
  </div>;
}

export function EvaluationView({ value }: { value: Record<string, unknown> }) {
  const evaluation = getEvaluation(value);
  const scores = asObject(evaluation.scores);
  const score = value.score ?? evaluation.score ?? evaluation.total_score;
  const verdict = value.verdict ?? evaluation.verdict;
  return <div className="stack">
    <Alert title="Full application evaluation">
      This assessment uses the server’s evaluation framework. Scores are advisory comparisons,
      not the probability of an interview or offer.
    </Alert>
    {(score != null || verdict != null) && <div className="actions">
      {score != null && <strong className="score-number">{displayValue(score)}<span> / 100</span></strong>}
      {verdict != null && <Badge value={displayValue(verdict)} />}
    </div>}
    {Object.keys(scores).length > 0 && <dl className="score-grid">{Object.entries(scores).map(([name, item]) =>
      <div key={name}><dt>{humanize(name)}</dt><dd>{displayValue(item)}<span> / 100</span></dd></div>)}</dl>}
    <div className="gate-grid">
      <GateView name="Language" value={evaluation.language ?? evaluation.language_gate} note={String(evaluation.language_note ?? '')} />
      <GateView name="Location" value={evaluation.location ?? evaluation.location_verdict} />
      <GateView name="Eligibility" value={evaluation.eligibility ?? evaluation.eligibility_gate} />
    </div>
    <StrengthsAndGaps strengths={evaluation.strengths} gaps={evaluation.gaps} />
    {evaluation.recommendation != null && <div><h3>Recommendation</h3>
      <p className="preserve-lines">{displayValue(evaluation.recommendation)}</p></div>}
    {hasBlockingGate(value) && <Alert tone="danger" title="Preparation is blocked">
      A required language, location, or eligibility gate failed. Correct any inaccurate profile or posting facts
      and run a new evaluation. A failed gate cannot be overridden here.
    </Alert>}
    {Array.isArray(evaluation.requirements) && <details><summary>Requirements & supporting evidence</summary>
      <StructuredDetails value={evaluation.requirements} /></details>}
    {value.company_research != null && <details><summary>Company research & sources</summary>
      <StructuredDetails value={value.company_research} /></details>}
  </div>;
}

function RewriteComparison({ before, after }: { before: Record<string, unknown>; after: Record<string, unknown> }) {
  const [kind, setKind] = useState<DocumentKind>('cv');
  return <div className="stack">
    <Alert title="Proposed rewrite · not yet accepted">
      Compare the proposed text with your saved drafts. Approving accepts the changes for compilation and checking;
      rejecting preserves the current content. No documents are sent.
    </Alert>
    <div className="segmented" role="group" aria-label="Rewrite document comparison">
      <button aria-pressed={kind === 'cv'} onClick={() => setKind('cv')}>Compare CV</button>
      <button aria-pressed={kind === 'cover_letter'} onClick={() => setKind('cover_letter')}>Compare cover letter</button>
    </div>
    <div className="comparison">
      <section><h3>Before · saved draft</h3><ReadDocument content={before[kind] as DocumentContent} /></section>
      <section><h3>After · proposed rewrite</h3><ReadDocument content={after[kind] as DocumentContent} /></section>
    </div>
  </div>;
}

export function ProposalView({ proposal, kind }: { proposal: Record<string, unknown>; kind: string }) {
  const before = asObject(proposal.before);
  const after = asObject(proposal.after);
  if (kind.includes('document_rewrite') && ['cv', 'cover_letter'].every((name) =>
    Array.isArray(asObject(before[name]).blocks) && Array.isArray(asObject(after[name]).blocks))) {
    return <RewriteComparison before={before} after={after} />;
  }
  if (kind.includes('posting_import') && Object.keys(asObject(proposal.record)).length) {
    const record = asObject(proposal.record);
    const other = Object.fromEntries(Object.entries(proposal).filter(([key]) => key !== 'record'));
    return <div className="stack">
      <Alert title="Confirm the posting before it is imported">
        Check the employer, title, location, and original text. The inferred metadata is a proposal, not a confirmed fact.
        Approving imports this record; it does not prepare or submit an application.
      </Alert>
      <section><h3>{typeof record.title === 'string' && record.title ? record.title : 'Title not provided'}</h3>
        <p>{typeof record.company === 'string' && record.company ? record.company : 'Employer not provided'}</p>
        <dl className="metadata"><dt>Location</dt><dd>{displayValue(record.location)}</dd><dt>Deadline</dt>
          <dd>{formatDate(typeof record.deadline === 'string' ? record.deadline : null)}</dd></dl>
        <ExternalLink href={typeof record.url === 'string' ? record.url : undefined}>Review original source</ExternalLink>
      </section>
      <section><h3>Original posting text</h3><div className="posting-text">{displayValue(record.description)}</div></section>
      {Array.isArray(record.warnings) && record.warnings.map((warning, index) => <Alert key={index} tone="warning">{displayValue(warning)}</Alert>)}
      <details><summary>Source & change-protection details</summary><StructuredDetails value={{ record, ...other }} /></details>
    </div>;
  }
  if (proposal.before !== undefined && proposal.after !== undefined) {
    const extra = Object.fromEntries(Object.entries(proposal).filter(([key]) => !['before', 'after'].includes(key)));
    return <div className="stack">
      <p>Review all changes below. The current files remain unchanged until you approve.</p>
      <div className="comparison">
        <section><h3>Before · current</h3><StructuredDetails value={proposal.before} /></section>
        <section><h3>After · proposed</h3><StructuredDetails value={proposal.after} /></section>
      </div>
      {Object.keys(extra).length > 0 && <details><summary>Files & supporting details</summary><StructuredDetails value={extra} /></details>}
    </div>;
  }
  if ('evaluation' in proposal || 'scores' in proposal || kind.includes('evaluation') || kind.includes('drafting')) {
    return <EvaluationView value={proposal} />;
  }
  return <div className="proposal-details"><StructuredDetails value={proposal} /></div>;
}
