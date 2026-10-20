import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ProfileEditor } from '../components/ProfileEditor';
import { MAX_DOCUMENT_BYTES, ProfileImport, validateProfileFile } from '../components/ProfileImport';
import { fetchFixtures, json, renderApp } from './helpers';
import { profileFixture } from './fixtures';

describe('profile editing and import', () => {
  it('sends a complete typed proposal, including repeatable work, education, projects and languages', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({ '/api/v1/profile/changes': { run_id: 'profile-run' } });
    renderApp(<ProfileEditor view={profileFixture} />);
    await user.clear(screen.getByLabelText('Full name'));
    await user.type(screen.getByLabelText('Full name'), 'Corrected Name');
    await user.click(screen.getByRole('button', { name: '+ Add experience' }));
    await user.type(screen.getAllByLabelText('Job title')[1], 'Team lead');
    await user.type(screen.getAllByLabelText('Employer')[1], 'Second Example');
    await user.click(screen.getByRole('button', { name: '+ Add education' }));
    await user.type(screen.getByLabelText('Degree or qualification'), 'BSc Computing');
    await user.type(screen.getByLabelText('Institution'), 'Example University');
    await user.click(screen.getByRole('button', { name: '+ Add project' }));
    await user.type(screen.getByLabelText('Project name'), 'Reporting project');
    await user.type(screen.getByLabelText('Project achievements'), 'Measured a real improvement.');
    await user.click(screen.getByRole('button', { name: '+ Add language' }));
    await user.type(screen.getAllByLabelText('Language', { exact: true })[1], 'Danish');
    await user.type(screen.getAllByLabelText('Proficiency')[1], 'B2');
    await user.clear(screen.getByLabelText('Skills', { exact: true }));
    await user.type(screen.getByLabelText('Skills', { exact: true }), 'TypeScript{Enter}React');
    await user.click(screen.getByRole('button', { name: 'Review profile changes' }));
    await screen.findByText(/Your proposed profile is waiting for review/);
    expect(fetch).toHaveBeenCalledTimes(1);
    const body = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(body.expected_revision).toBe('profile-r1');
    expect(body.profile).toMatchObject({
      name: 'Corrected Name', skills: ['TypeScript', 'React'],
      languages: [{ name: 'English', level: 'Professional' }, { name: 'Danish', level: 'B2' }],
      education: [{ degree: 'BSc Computing', institution: 'Example University', details: '' }],
      projects: [{ name: 'Reporting project', bullets: ['Measured a real improvement.'] }],
    });
    expect(body.profile.experiences).toHaveLength(2);
    expect(body.profile.experiences[1]).toMatchObject({ title: 'Team lead', company: 'Second Example' });
    expect(body.profile).toHaveProperty('writing_style', '');
    expect(body.profile).toHaveProperty('behavioral', '');
    expect(screen.getByLabelText('Full name')).toHaveValue('Corrected Name');
  });

  it('preserves edits and explains server revision conflicts', async () => {
    const user = userEvent.setup();
    fetchFixtures({ '/api/v1/profile/changes': () => json({
      error: { code: 'REVISION_CONFLICT', message: 'Confirmed facts changed.' },
    }, 409) });
    const { rerender } = renderApp(<ProfileEditor view={profileFixture} />);
    await user.type(screen.getByLabelText('Professional headline'), 'My unsaved headline');
    rerender(<ProfileEditor view={{ ...profileFixture, revision: 'profile-r2' }} />);
    expect(screen.getByText('A newer confirmed profile is available')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Review profile changes' }));
    expect(await screen.findByText('Confirmed facts changed.')).toBeVisible();
    expect(screen.getByLabelText('Professional headline')).toHaveValue('My unsaved headline');
  });

  it('validates file size, type, and empty uploads before sending data', () => {
    expect(validateProfileFile()).toMatch(/Choose/);
    expect(validateProfileFile(new File(['text'], 'scan.png'))).toMatch(/text-readable/);
    expect(validateProfileFile(new File([], 'empty.txt'))).toMatch(/empty/);
    const oversized = new File(['text'], 'large.pdf');
    Object.defineProperty(oversized, 'size', { value: MAX_DOCUMENT_BYTES + 1 });
    expect(validateProfileFile(oversized)).toMatch(/20 MiB/);
    expect(validateProfileFile(new File(['career notes'], 'profile.md'))).toBeUndefined();
  });

  it('uploads a document as multipart and sends extracted facts to approval, not directly to profile', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({ '/api/v1/documents/imports': { run_id: 'import-run' } });
    renderApp(<ProfileImport />);
    await user.upload(screen.getByLabelText('Profile document'), new File(['Real career facts'], 'profile.txt', { type: 'text/plain' }));
    await user.click(screen.getByRole('button', { name: 'Import & review facts' }));
    await waitFor(() => expect(screen.getByLabelText('Profile document')).toHaveValue(''));
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/documents/imports');
    const body = fetch.mock.calls[0][1]?.body as FormData;
    expect((body.get('file') as File).name).toBe('profile.txt');
    expect(screen.getByRole('link', { name: /Follow progress/ })).toHaveAttribute('href', '/activity#run-import-run');
  });
});
