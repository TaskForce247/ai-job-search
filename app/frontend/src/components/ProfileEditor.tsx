import { useEffect, useState } from 'react';
import { Controller, useFieldArray, useForm } from 'react-hook-form';
import type { Control } from 'react-hook-form';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAction, useNotices } from '../query';
import type { ApiSchema, Profile, ProfileView, RunAccepted } from '../types';
import { Alert, Button, ErrorPanel, Field, Modal, Panel } from './ui';

type LineName = 'skills' | 'goals' | 'deal_breakers' |
  `experiences.${number}.bullets` | `projects.${number}.bullets`;

function Lines({ control, name, label, max = 40, hint }: {
  control: Control<Profile>; name: LineName; label: string; max?: number; hint?: string;
}) {
  const id = `profile-${name.replaceAll('.', '-')}`;
  return <Controller name={name} control={control}
    rules={{ validate: (value) => value.filter((line) => line.trim()).length <= max || `Use at most ${max} lines.` }}
    render={({ field, fieldState }) => <Field id={id} label={label}
      hint={hint ?? 'One item per line. Leave unknown information blank.'} error={fieldState.error?.message}>
      <textarea id={id} rows={4} {...field} value={field.value.join('\n')}
        aria-invalid={!!fieldState.error} aria-describedby={`${id}-hint`}
        onChange={(event) => field.onChange(event.target.value.split(/\r?\n/))} />
    </Field>} />;
}

function cleanProfile(profile: Profile): Profile {
  const clean = (lines: string[]) => lines.map((line) => line.trim()).filter(Boolean);
  return {
    ...profile,
    skills: clean(profile.skills), goals: clean(profile.goals), deal_breakers: clean(profile.deal_breakers),
    experiences: profile.experiences.map((experience) => ({ ...experience, bullets: clean(experience.bullets) })),
    projects: profile.projects.map((project) => ({ ...project, bullets: clean(project.bullets) })),
  };
}

export function ProfileEditor({ view }: { view: ProfileView }) {
  const { register, control, handleSubmit, reset, formState: { isDirty, errors } } = useForm<Profile>({
    defaultValues: view.data, mode: 'onBlur',
  });
  const [baseRevision, setBaseRevision] = useState(view.revision);
  const [discardOpen, setDiscardOpen] = useState(false);
  const { queued } = useNotices();
  const experiences = useFieldArray({ control, name: 'experiences', keyName: 'formKey' });
  const education = useFieldArray({ control, name: 'education', keyName: 'formKey' });
  const projects = useFieldArray({ control, name: 'projects', keyName: 'formKey' });
  const languages = useFieldArray({ control, name: 'languages', keyName: 'formKey' });
  const action = useAction(
    (profile: Profile) => api<RunAccepted>('/profile/changes', {
      method: 'POST', body: { profile: cleanProfile(profile), expected_revision: baseRevision } satisfies ApiSchema<'ProfileChange'>,
    }),
    (run) => queued(run, 'Your proposed profile is waiting for review. Confirm the facts in Activity before they are used.'),
  );

  useEffect(() => {
    if (view.revision !== baseRevision && !isDirty) {
      reset(view.data); setBaseRevision(view.revision);
    }
  }, [view, baseRevision, isDirty, reset]);

  function reload() {
    reset(view.data); setBaseRevision(view.revision); setDiscardOpen(false); action.reset();
  }

  return <form onSubmit={handleSubmit((profile) => action.mutate(profile))} className="stack">
    <Alert title="Your facts, with your approval">
      Saving creates a proposal, not an immediate change. Review the before-and-after in <Link to="/activity">Activity</Link>.
      Only confirmed facts become evidence for applications. You can save a partially completed profile.
    </Alert>
    {view.revision !== baseRevision && <Alert tone="warning" title="A newer confirmed profile is available">
      Your current edits are still here. Reload the confirmed version before making another proposal.
      <Button variant="secondary" type="button" onClick={() => setDiscardOpen(true)}>Review latest version</Button>
    </Alert>}
    <ErrorPanel error={action.error} />
    {Object.keys(errors).length > 0 && <Alert tone="danger">Check the highlighted profile fields before continuing.</Alert>}
    <Panel title="The essentials">
      <div className="form-grid">
        <Field id="profile-name" label="Full name"><input id="profile-name" autoComplete="name" maxLength={500} {...register('name')} /></Field>
        <Field id="profile-headline" label="Professional headline"><input id="profile-headline" maxLength={500} {...register('headline')} /></Field>
        <Field id="profile-email" label="Email" error={errors.email?.message}>
          <input id="profile-email" type="email" autoComplete="email" maxLength={500} aria-invalid={!!errors.email}
            {...register('email', { validate: (value) => !value || /^[^\s@]+@[^\s@]+$/.test(value) || 'Enter a valid email address.' })} />
        </Field>
        <Field id="profile-phone" label="Phone"><input id="profile-phone" type="tel" autoComplete="tel" maxLength={500} {...register('phone')} /></Field>
        <Field id="profile-location" label="Location"><input id="profile-location" autoComplete="address-level2" maxLength={500} {...register('location')} /></Field>
        <Field id="profile-status" label="Current employment situation"><input id="profile-status" maxLength={500} {...register('employment_status')} /></Field>
        <Field id="profile-linkedin" label="LinkedIn URL"><input id="profile-linkedin" type="url" maxLength={500} {...register('linkedin')} /></Field>
        <Field id="profile-github" label="GitHub or portfolio URL"><input id="profile-github" type="url" maxLength={500} {...register('github')} /></Field>
      </div>
      <Field id="profile-summary" label="Professional summary" hint="Describe your actual experience. No need to write a finished CV.">
        <textarea id="profile-summary" rows={5} maxLength={20_000} {...register('summary')} />
      </Field>
    </Panel>
    <Panel title="Work history" actions={<Button type="button" variant="secondary"
      disabled={experiences.fields.length >= 60} onClick={() => experiences.append({
        id: crypto.randomUUID(), title: '', company: '', location: '', start: '', end: '', bullets: [],
      })}>+ Add experience</Button>}>
      {experiences.fields.length === 0 && <p className="hint">Add employment, volunteering, or other relevant experience. Nothing is required or invented.</p>}
      <div className="stack">{experiences.fields.map((item, index) => <fieldset key={item.formKey} className="repeater">
        <legend>Experience {index + 1}</legend>
        <div className="form-grid">
          <Field id={`exp-title-${index}`} label="Job title"><input id={`exp-title-${index}`} maxLength={500} {...register(`experiences.${index}.title`)} /></Field>
          <Field id={`exp-company-${index}`} label="Employer"><input id={`exp-company-${index}`} maxLength={500} {...register(`experiences.${index}.company`)} /></Field>
          <Field id={`exp-location-${index}`} label="Work location"><input id={`exp-location-${index}`} maxLength={500} {...register(`experiences.${index}.location`)} /></Field>
          <Field id={`exp-start-${index}`} label="Start date or year"><input id={`exp-start-${index}`} maxLength={500} {...register(`experiences.${index}.start`)} /></Field>
          <Field id={`exp-end-${index}`} label="End date or Present"><input id={`exp-end-${index}`} maxLength={500} {...register(`experiences.${index}.end`)} /></Field>
        </div>
        <Lines control={control} name={`experiences.${index}.bullets`} label="Responsibilities and achievements" />
        <Button type="button" variant="ghost" onClick={() => experiences.remove(index)}>Remove experience {index + 1}</Button>
      </fieldset>)}</div>
    </Panel>
    <Panel title="Education" actions={<Button type="button" variant="secondary"
      disabled={education.fields.length >= 30} onClick={() => education.append({
        id: crypto.randomUUID(), degree: '', institution: '', start: '', end: '', details: '',
      })}>+ Add education</Button>}>
      {education.fields.length === 0 && <p className="hint">Degrees, training, and qualifications you want to include.</p>}
      <div className="stack">{education.fields.map((item, index) => <fieldset className="repeater" key={item.formKey}>
        <legend>Education {index + 1}</legend>
        <div className="form-grid">
          <Field id={`edu-degree-${index}`} label="Degree or qualification"><input id={`edu-degree-${index}`} maxLength={500} {...register(`education.${index}.degree`)} /></Field>
          <Field id={`edu-school-${index}`} label="Institution"><input id={`edu-school-${index}`} maxLength={500} {...register(`education.${index}.institution`)} /></Field>
          <Field id={`edu-start-${index}`} label="Start year"><input id={`edu-start-${index}`} maxLength={500} {...register(`education.${index}.start`)} /></Field>
          <Field id={`edu-end-${index}`} label="End year"><input id={`edu-end-${index}`} maxLength={500} {...register(`education.${index}.end`)} /></Field>
        </div>
        <Field id={`edu-details-${index}`} label="Education details"><textarea id={`edu-details-${index}`} maxLength={20_000} rows={3} {...register(`education.${index}.details`)} /></Field>
        <Button type="button" variant="ghost" onClick={() => education.remove(index)}>Remove education {index + 1}</Button>
      </fieldset>)}</div>
    </Panel>
    <Panel title="Projects" actions={<Button type="button" variant="secondary"
      disabled={projects.fields.length >= 40} onClick={() => projects.append({
        id: crypto.randomUUID(), name: '', description: '', bullets: [],
      })}>+ Add project</Button>}>
      {projects.fields.length === 0 && <p className="hint">Personal, academic, or professional projects backed by real experience.</p>}
      <div className="stack">{projects.fields.map((item, index) => <fieldset className="repeater" key={item.formKey}>
        <legend>Project {index + 1}</legend>
        <Field id={`project-name-${index}`} label="Project name"><input id={`project-name-${index}`} maxLength={500} {...register(`projects.${index}.name`)} /></Field>
        <Field id={`project-description-${index}`} label="Project description"><textarea id={`project-description-${index}`} rows={3} maxLength={20_000} {...register(`projects.${index}.description`)} /></Field>
        <Lines control={control} name={`projects.${index}.bullets`} label="Project achievements" max={30} />
        <Button type="button" variant="ghost" onClick={() => projects.remove(index)}>Remove project {index + 1}</Button>
      </fieldset>)}</div>
    </Panel>
    <Panel title="Languages" actions={<Button type="button" variant="secondary"
      disabled={languages.fields.length >= 40} onClick={() => languages.append({ name: '', level: '' })}>+ Add language</Button>}>
      <p className="hint">List every language you can work in and your real level. Required languages affect job eligibility.</p>
      {languages.fields.map((item, index) => <fieldset className="repeater" key={item.formKey}>
        <legend>Language {index + 1}</legend><div className="form-grid">
          <Field id={`language-name-${index}`} label="Language" error={errors.languages?.[index]?.name?.message}>
            <input id={`language-name-${index}`} maxLength={500} {...register(`languages.${index}.name`, { required: 'Name this language or remove the empty row.' })} />
          </Field>
          <Field id={`language-level-${index}`} label="Proficiency" hint="For example: native, professional, or a CEFR level."
            error={errors.languages?.[index]?.level?.message}>
            <input id={`language-level-${index}`} maxLength={500} {...register(`languages.${index}.level`, { required: 'Describe your proficiency.' })} />
          </Field>
        </div><Button type="button" variant="ghost" onClick={() => languages.remove(index)}>Remove language {index + 1}</Button>
      </fieldset>)}
      <Field id="profile-cv-language" label="CV language"><input id="profile-cv-language" maxLength={500} {...register('cv_language')} /></Field>
    </Panel>
    <Panel title="Skills and career preferences">
      <Lines control={control} name="skills" label="Skills" max={200} hint="One skill per line. Include only skills you can substantiate." />
      <Lines control={control} name="goals" label="Target roles, goals, and working preferences" hint="One item per line. Include sectors, remote work, travel, or commute preferences." />
      <Lines control={control} name="deal_breakers" label="Hard restrictions" hint="One item per line. Leave sensitive details out unless they matter to your search." />
      <Field id="profile-behavioral" label="Working style (optional)"><textarea id="profile-behavioral" rows={4} maxLength={20_000} {...register('behavioral')} /></Field>
      <Field id="profile-writing" label="Writing preferences (optional)"><textarea id="profile-writing" rows={4} maxLength={20_000} {...register('writing_style')} /></Field>
    </Panel>
    <div className="sticky-actions">
      <p>{isDirty ? 'Unconfirmed edits · review required' : 'Confirmed profile · changes require review'}</p>
      <div className="actions">
        <Button type="button" variant="secondary" disabled={!isDirty || action.isPending}
          onClick={() => setDiscardOpen(true)}>Discard edits</Button>
        <Button type="submit" busy={action.isPending}>Review profile changes</Button>
      </div>
    </div>
    <Modal open={discardOpen} onClose={() => setDiscardOpen(false)} title="Reload the confirmed profile?">
      <p>Your unsaved edits will be discarded. The confirmed profile will not be changed.</p>
      <div className="actions"><Button type="button" variant="secondary" onClick={() => setDiscardOpen(false)}>Keep editing</Button>
        <Button type="button" onClick={reload}>Reload confirmed profile</Button></div>
    </Modal>
  </form>;
}
