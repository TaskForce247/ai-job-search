import type { DocumentBlock, DocumentContent } from '../types';
import { ReadDocument } from './PdfPreview';
import { Alert } from './ui';

function wording(block: DocumentBlock) {
  return JSON.stringify([block.kind, block.heading, block.organization, block.location, block.start,
    block.end, block.text, block.bullets.map((bullet) => bullet.text)]);
}

function evidence(block: DocumentBlock) {
  return JSON.stringify([block.evidence_ids, block.bullets.map((bullet) => bullet.evidence_ids)]);
}

function matchBlocks(before: DocumentContent, after: DocumentContent) {
  const matches = new Map<string, DocumentBlock>();
  const used = new Set<string>();
  for (const block of after.blocks) {
    const original = before.blocks.find((item) => item.id === block.id);
    if (original) { matches.set(block.id, original); used.add(original.id); }
  }
  for (const block of after.blocks) {
    if (matches.has(block.id)) continue;
    const original = before.blocks.find((item) => !used.has(item.id) && wording(item) === wording(block));
    if (original) { matches.set(block.id, original); used.add(original.id); }
  }
  return matches;
}

export function documentChanges(before: DocumentContent, after: DocumentContent) {
  const matches = matchBlocks(before, after);
  const used = new Set([...matches.values()].map((block) => block.id));
  const added = after.blocks.length - matches.size;
  const removed = before.blocks.length - matches.size;
  const changed = after.blocks.filter((block) => matches.has(block.id) &&
    wording(matches.get(block.id)!) !== wording(block)).length;
  const relinked = after.blocks.filter((block) => matches.has(block.id) &&
    evidence(matches.get(block.id)!) !== evidence(block)).length;
  const oldOrder = before.blocks.filter((block) => used.has(block.id)).map((block) => block.id);
  const newOrder = after.blocks.flatMap((block) => matches.has(block.id) ? [matches.get(block.id)!.id] : []);
  const reordered = JSON.stringify(oldOrder) !== JSON.stringify(newOrder);
  const languageChanged = before.language !== after.language;
  return { added, removed, changed, relinked, reordered, languageChanged,
    noTextChanges: !added && !removed && !changed && !reordered && !languageChanged };
}

export function DocumentComparison({ before, after, beforeTitle = 'Before · saved draft', afterTitle = 'After · proposed rewrite' }: {
  before: DocumentContent; after: DocumentContent; beforeTitle?: string; afterTitle?: string;
}) {
  const changes = documentChanges(before, after);
  const matches = matchBlocks(before, after);
  function renderSide(content: DocumentContent, other: DocumentContent, side: 'before' | 'after') {
    return content.blocks.map((block) => {
      const counterpart = side === 'after' ? matches.get(block.id)
        : other.blocks.find((item) => matches.get(item.id)?.id === block.id);
      const changed = !counterpart || wording(block) !== wording(counterpart);
      const document = <ReadDocument content={{ language: content.language, blocks: [block] }} />;
      return <div key={block.id} className={changed ? `diff-block diff-${side}` : 'diff-block'}>
        {changed && <span className="diff-label">{side === 'before' ? 'Removed or replaced' : 'Added or revised'}</span>}
        {changed ? side === 'before' ? <del>{document}</del> : <ins>{document}</ins> : document}
      </div>;
    });
  }
  return <div className="stack">
    {changes.noTextChanges && <Alert title="No text changes">
      {changes.relinked ? 'Only supporting evidence links changed. The document wording is unchanged.'
        : 'The proposed document has the same wording and order as the saved draft.'}
    </Alert>}
    <p className="hint" role="status">{changes.added} blocks added · {changes.removed} removed · {changes.changed} revised
      {changes.relinked > 0 && ` · ${changes.relinked} evidence links updated`}
      {changes.reordered && ' · block order changed'}
      {changes.languageChanged && ` · language: ${before.language} → ${after.language}`}</p>
    <div className="comparison">
      <section><h3>{beforeTitle}</h3>{renderSide(before, after, 'before')}</section>
      <section><h3>{afterTitle}</h3>{renderSide(after, before, 'after')}</section>
    </div>
  </div>;
}
