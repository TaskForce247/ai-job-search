import type { components } from './api/schema';

export type ApiSchema<Name extends keyof components['schemas']> = components['schemas'][Name];
type KnownFields<T> = { [Key in keyof T as string extends Key ? never : number extends Key ? never : Key]: T[Key] };
export type Language = ApiSchema<'Language'>;
export type Experience = Required<ApiSchema<'Experience'>>;
export type Education = Required<ApiSchema<'Education'>>;
export type Project = Required<ApiSchema<'Project'>>;
export type Evidence = ApiSchema<'Evidence'>;

// Pydantic fills these arrays and identifiers on reads; forms keep known-empty
// values explicit rather than introducing undefined or invented candidate facts.
export type Profile = Omit<Required<ApiSchema<'Profile'>>, 'experiences' | 'education' | 'projects'> & {
  experiences: Experience[];
  education: Education[];
  projects: Project[];
};
export type ProfileView = Omit<ApiSchema<'ProfileView'>, 'data' | 'warnings'> & {
  data: Profile;
  warnings: string[];
};
export type Settings = ApiSchema<'Settings'>;
export type SettingsView = ApiSchema<'SettingsView'>;

export interface Workspace {
  path: string;
  revision: string;
  initialized: boolean;
  public_repository_warning: boolean | string;
}

export interface Dependency {
  id: string;
  label: string;
  status: 'ready' | 'optional' | 'setup_required' | 'unavailable';
  description: string;
  repair_url?: string | null;
  can_install?: boolean;
}

export interface Readiness {
  dependencies: Dependency[];
  ai_connected: boolean;
  privacy_consent: boolean;
  workspace_path: string;
  active_runs: number;
  version: string;
}

export interface Portal {
  id: string;
  label: string;
  enabled: boolean;
  installed_enabled: boolean;
  effective_enabled: boolean;
  supported: boolean;
  warning: string | null;
  health: string | Record<string, unknown>;
  revision: string;
}

export interface Gate {
  verdict: 'PASS' | 'FAIL' | 'FLAG' | string;
  note?: string;
}

export type Job = KnownFields<ApiSchema<'JobView'>> & {
  strengths: string[];
  gaps: string[];
  language_gate?: string | Gate | null;
  language_note?: string | null;
  location_verdict?: string | Gate | null;
  eligibility_gate?: string | Gate | null;
  last_fetch_error?: string | null;
  expired_reason?: string | null;
  warnings: string[];
};

export type Page<T> = Pick<ApiSchema<'Page_JobView_'>, 'next_cursor' | 'total'> & {
  items: T[];
};

export type RunStatus = ApiSchema<'RunView'>['status'];

export type Approval = KnownFields<ApiSchema<'ApprovalView'>>;

export interface Artifact {
  id: string;
  name?: string;
  label?: string;
  kind?: string;
  media_type?: string;
}

export type Run = Omit<KnownFields<ApiSchema<'RunView'>>, 'result' | 'approvals' | 'artifacts'> & {
  result: Record<string, unknown> | null;
  approvals: Approval[];
  artifacts: Artifact[];
};

export type RunAccepted = ApiSchema<'Queued'> & {
  preparation_run_id?: string | null;
};

export type Claim = Required<ApiSchema<'Claim'>>;

export type DocumentBlock = Omit<Required<ApiSchema<'DocumentBlock'>>, 'bullets'> & {
  bullets: Claim[];
};

export type DocumentContent = Omit<ApiSchema<'DocumentContent-Output'>, 'blocks'> & {
  blocks: DocumentBlock[];
};

export type DocumentCheck = ApiSchema<'Check'>;

export type DocumentView = Omit<KnownFields<ApiSchema<'DocumentView'>>, 'content' | 'keyword_coverage'> & {
  content: DocumentContent;
  keyword_coverage?: {
    term: string;
    priority: string;
    status: string;
    note: string;
  }[];
};

export type DocumentKind = 'cv' | 'cover_letter';

export type Application = Omit<KnownFields<ApiSchema<'ApplicationView'>>, 'documents' | 'evaluation' | 'review' | 'timeline'> & {
  documents: Partial<Record<DocumentKind, DocumentView>>;
  evaluation?: Record<string, unknown> | null;
  review?: {
    findings: { kind: string; message: string; block_id?: string }[];
    sources: { url: string; claim: string; quote: string }[];
  } | null;
  timeline: { date: string; status: string; stage: string; notes: string }[];
};

export interface Dashboard {
  counts: {
    drafted: number;
    submitted: number;
    active: number;
    interview: number;
    offer: number;
    hired: number;
    closed: number;
  };
  funnel: { stage: string; count: number }[];
  deadlines: Application[];
  recent_applications: Application[];
  pending_approvals: number;
}

export const emptyProfile: Profile = {
  name: '', email: '', phone: '', location: '', linkedin: '', github: '',
  headline: '', summary: '', employment_status: '', cv_language: 'English',
  languages: [], skills: [], goals: [], deal_breakers: [], experiences: [],
  education: [], projects: [], behavioral: '', writing_style: '',
};

export const outcomeStatuses = [
  'applied', 'interview', 'offer', 'hired', 'rejected', 'no_response',
  'offer_declined', 'withdrawn',
] as const;
