import type { Application, Approval, DocumentView, Job, ProfileView, Run, SettingsView, Workspace } from '../types';
import { emptyProfile } from '../types';

export const workspaceFixture: Workspace = {
  path: 'C:\\Synthetic Career Workspace', revision: 'workspace-r1',
  initialized: true, public_repository_warning: false,
};

export const profileFixture: ProfileView = {
  data: {
    ...emptyProfile, name: 'Ada Example', email: 'ada@example.test', location: 'Sample City',
    skills: ['TypeScript'], languages: [{ name: 'English', level: 'Professional' }],
    experiences: [{ id: 'experience-1', title: 'Developer', company: 'Example Co', location: '', start: '2023', end: 'Present', bullets: ['Built a reporting tool.'] }],
  },
  revision: 'profile-r1',
  evidence: [{ id: 'fact-1', source: 'Confirmed profile', section: 'Experience', text: 'Built a reporting tool.' }],
  warnings: [],
};

export const settingsFixture: SettingsView = {
  settings: {
    model: 'claude-sonnet-4-6', privacy_consent: true, max_run_cost_usd: 3, max_turns: 8,
    stage_timeout_seconds: 180, search_location: '', search_country: '', search_query: '', onboarding_complete: true,
  },
  revision: 'settings-r1', ai_connected: true,
};

export const documentFixture: DocumentView = {
  revision: 'document-r1', stale: true, pdf_artifact_id: 'pdf-old',
  content: { language: 'English', blocks: [
    { id: 'block-1', kind: 'paragraph', text: 'Original paragraph.', heading: '', organization: '', location: '', start: '', end: '', evidence_ids: ['fact-1'], bullets: [] },
    { id: 'block-2', kind: 'heading', text: '', heading: 'Experience', organization: '', location: '', start: '', end: '', evidence_ids: [], bullets: [] },
  ] },
  checks: [{ id: 'pages', label: 'Page count', status: 'pass', message: 'Exactly two pages.' },
    { id: 'visual', label: 'Visual inspection', status: 'skipped', message: 'Not available.' }],
  keyword_coverage: [{ term: 'TypeScript', priority: 'required', status: 'covered', note: 'Confirmed experience.' }],
};

export const applicationFixture: Application = {
  id: 'application-1', company: 'Example Co', role: 'Software developer', status: 'drafted',
  source: 'https://example.test/careers/developer', deadline: null, applied_on: null, fit_rating: 82,
  revision: 'application-r1', archive_dir: 'documents/applications/example_application-1',
  documents: { cv: documentFixture }, timeline: [], published: true, legacy: false,
};

export const approvalFixture: Approval = {
  id: 'approval-1', run_id: 'run-1', kind: 'profile_change', title: 'Confirm extracted profile',
  revision: 'approval-r1', status: 'pending', choices: ['approve', 'reject'],
  proposal: { before: { name: 'Current name' }, after: { name: 'Proposed name' } },
};

export const runFixture: Run = {
  id: 'run-1', kind: 'profile_import', status: 'awaiting_user', stage: 'confirm_profile',
  message: 'Review the extracted facts.', created_at: '2026-10-19T10:00:00Z', updated_at: '2026-10-19T10:01:00Z',
  input: {}, result: null, error: null, error_code: null, cost_usd: 0.12, approvals: [approvalFixture], artifacts: [],
};

export const jobFixture: Job = {
  id: 'job-1', key: 'example-key', title: 'Software developer', company: 'Example Co', location: 'Remote',
  url: 'https://example.test/jobs/1', portal: 'freehire', source: 'freehire', description: '<script>alert("untrusted")</script>\nBuild real things.',
  first_seen: '2026-10-19T10:00:00Z', posted_date: null, deadline: null, status: 'new', fit: 'Potential match',
  strengths: [], gaps: [], warnings: [], saved: false, revision: 'job-r1',
};
