import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DocumentComparison, documentChanges } from '../components/DocumentComparison';
import { documentFixture } from './fixtures';
import { renderApp } from './helpers';

describe('document change comparisons', () => {
  it('explicitly identifies an unchanged proposal', () => {
    renderApp(<DocumentComparison before={documentFixture.content} after={structuredClone(documentFixture.content)} />);
    expect(screen.getByText('No text changes')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('0 blocks added · 0 removed · 0 revised');
    expect(document.querySelector('ins, del')).toBeNull();
  });

  it('marks replaced wording and includes changed structured fields and bullets', () => {
    const after = structuredClone(documentFixture.content);
    after.blocks[0].text = 'Revised supported experience.';
    after.blocks[0].organization = 'New employer';
    after.blocks[0].bullets = [{ id: 'b1', text: 'New bullet.', evidence_ids: ['fact-1'] }];
    renderApp(<DocumentComparison before={documentFixture.content} after={after} />);
    expect(screen.queryByText('No text changes')).not.toBeInTheDocument();
    expect(screen.getByText('Original paragraph.').closest('del')).not.toBeNull();
    expect(screen.getByText('Revised supported experience.').closest('ins')).not.toBeNull();
    expect(screen.getByText('New employer').closest('ins')).not.toBeNull();
    expect(screen.getByText('New bullet.').closest('ins')).not.toBeNull();
    expect(screen.getByRole('status')).toHaveTextContent('1 revised');
  });

  it('distinguishes evidence-only updates from wording changes', () => {
    const after = structuredClone(documentFixture.content);
    after.blocks[0].evidence_ids = ['different-fact'];
    renderApp(<DocumentComparison before={documentFixture.content} after={after} />);
    expect(screen.getByText('No text changes')).toBeVisible();
    expect(screen.getByText(/Only supporting evidence links changed/)).toBeVisible();
    expect(documentChanges(documentFixture.content, after).relinked).toBe(1);
  });

  it('reports additions, removals, reordered blocks and language changes', () => {
    const before = documentFixture.content;
    const after = structuredClone(before);
    after.blocks.reverse();
    after.language = 'Danish';
    expect(documentChanges(before, after)).toMatchObject({ reordered: true, languageChanged: true, noTextChanges: false });
    after.blocks[0].id = 'new-section';
    after.blocks[0].heading = 'New section';
    expect(documentChanges(before, after)).toMatchObject({ added: 1, removed: 1 });
  });

  it('does not mistake regenerated AI block identifiers for text changes', () => {
    const after = structuredClone(documentFixture.content);
    after.blocks.forEach((block, index) => { block.id = `regenerated-${index}`; });
    expect(documentChanges(documentFixture.content, after)).toMatchObject({
      noTextChanges: true, added: 0, removed: 0, changed: 0, reordered: false,
    });
    renderApp(<DocumentComparison before={documentFixture.content} after={after} />);
    expect(screen.getByText('No text changes')).toBeVisible();
    expect(document.querySelector('ins, del')).toBeNull();
  });
});
