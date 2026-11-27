'use client';

import {zodResolver} from '@hookform/resolvers/zod';
import {useMutation} from '@tanstack/react-query';
import {useFieldArray, useForm} from 'react-hook-form';
import {z} from 'zod';
import {patchProfile, type Bip, type ProfileResponse} from '@/api/generated/workspace';
import {Dialog, Field, Problem} from '@/components/shared/workflow';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {nullable, splitLines} from '@/lib/workflow';

const editable = {
  summary: {
    section: 'brand_identity', title: 'Edit brand summary',
    fields: [['name', 'Brand name', 'single'], ['tagline', 'Tagline', 'single'], ['description', 'Description', 'long']],
  },
  positioning: {
    section: 'business_positioning', title: 'Edit differentiators',
    fields: [['positioning_statement', 'Positioning statement', 'long'], ['differentiators', 'Differentiators', 'list']],
  },
  psychology: {
    section: 'customer_psychology', title: 'Edit customer psychology',
    fields: [
      ['pain_points', 'Pain points', 'list'], ['desired_outcomes', 'Desired outcomes', 'list'],
      ['motivations', 'Motivations', 'list'], ['fears', 'Fears', 'list'], ['objections', 'Objections', 'list'],
      ['purchase_triggers', 'Purchase triggers', 'list'], ['decision_criteria', 'Decision criteria', 'list'],
      ['emotional_benefits', 'Emotional benefits', 'list'], ['adjacent_interests', 'Adjacent interests', 'list'],
      ['relatable_situations', 'Relatable situations', 'list'], ['emotional_triggers', 'Emotional triggers', 'list'],
    ],
  },
  voice: {
    section: 'brand_voice', title: 'Edit brand voice',
    fields: [['tone', 'Tone', 'list'], ['language_style', 'Language style', 'long'],
      ['preferred_terms', 'Preferred terms', 'list'], ['avoided_terms', 'Terms to avoid', 'list']],
  },
} as const satisfies Record<string, {section: keyof Bip; title: string; fields: readonly (readonly [string, string, string])[]}>;

export type EditPanel = keyof typeof editable | 'audience';

const fieldsSchema = z.object({values: z.record(z.string(), z.string().max(8000))});
const audienceEntry = z.object({name: z.string().max(300), description: z.string().max(4000), sourceIndex: z.number().int().optional()});
const audienceSchema = z.object({segments: z.array(audienceEntry), personas: z.array(audienceEntry)});
type AudienceForm = z.infer<typeof audienceSchema>;

interface EditorProps {
  profile: ProfileResponse;
  close: () => void;
  saved: (record: ProfileResponse) => void;
  reload: () => void;
}

export function ProfileEditor(props: EditorProps & {panel: EditPanel}) {
  return props.panel === 'audience' ? <AudienceEditor {...props} /> : <FieldsEditor {...props} section={props.panel} />;
}

function useProfileSave({profile, saved}: EditorProps) {
  return useMutation({
    mutationFn: (changes: Record<string, unknown>) => patchProfile(profile.brand_id, {
      expected_revision: profile.revision, changes,
    }),
    onSuccess: saved,
  });
}

function FieldsEditor(props: EditorProps & {section: keyof typeof editable}) {
  const config = editable[props.section];
  const data: Record<string, unknown> = {...props.profile.profile[config.section]};
  const values = Object.fromEntries(config.fields.map(([key]) => {
    const value = data[key];
    return [key, Array.isArray(value) ? value.join('\n') : typeof value === 'string' ? value : ''];
  }));
  const {register, handleSubmit, formState: {errors}} = useForm<z.infer<typeof fieldsSchema>>({
    resolver: zodResolver(fieldsSchema), defaultValues: {values},
  });
  const save = useProfileSave(props);

  return <Dialog title={config.title} close={props.close}>
    <form className="workflow-form" onSubmit={handleSubmit(data => {
      const edits = Object.fromEntries(config.fields.map(([key, , type]) => [
        key, type === 'list' ? splitLines(data.values[key] ?? '') : nullable(data.values[key]),
      ]));
      save.mutate({[config.section]: edits});
    })}>
      {config.fields.map(([key, label, type]) => <Field key={key} label={label} error={errors.values?.[key]?.message}
        hint={type === 'list' ? 'One entry per line.' : undefined}>
        {type === 'single'
          ? <Input {...register(`values.${key}`)} maxLength={key === 'name' ? 300 : 1000} />
          : <textarea rows={type === 'long' ? 4 : 3} {...register(`values.${key}`)} maxLength={8000} />}
      </Field>)}
      <Problem error={save.error} reload={props.reload} />
      <div className="workflow-actions"><Button type="submit" disabled={save.isPending}>Save changes</Button>
        <Button variant="outline" onClick={props.close}>Cancel</Button></div>
    </form>
  </Dialog>;
}

function AudienceEditor(props: EditorProps) {
  const current = props.profile.profile.customer_personas_and_audience_segments;
  // Keep existing fields (goals, channels, needs) with each row while editing nearby copy.
  const rows = useForm<AudienceForm>({
    resolver: zodResolver(audienceSchema),
    defaultValues: {
      segments: current?.segments?.map((row, sourceIndex) => ({name: row.name ?? '', description: row.description ?? '', sourceIndex})) ?? [],
      personas: current?.personas?.map((row, sourceIndex) => ({name: row.name ?? '', description: row.description ?? '', sourceIndex})) ?? [],
    },
  });
  const segments = useFieldArray({control: rows.control, name: 'segments'});
  const personas = useFieldArray({control: rows.control, name: 'personas'});
  const save = useProfileSave(props);
  return <Dialog title="Edit audience" close={props.close}>
    <form className="workflow-form" onSubmit={rows.handleSubmit(values => {
      const merged = Object.fromEntries((['segments', 'personas'] as const).map(group => [
        group, values[group].filter(row => row.name.trim() || row.description.trim() || row.sourceIndex !== undefined)
          .map(row => ({
            ...(row.sourceIndex === undefined ? {} : current?.[group]?.[row.sourceIndex]),
            name: nullable(row.name), description: nullable(row.description),
          })),
      ]));
      save.mutate({customer_personas_and_audience_segments: merged});
    })}>
      {([['segments', segments], ['personas', personas]] as const).map(([kind, group]) => <section key={kind} className="workflow-form">
        <h3>{kind === 'segments' ? 'Audience segments' : 'Personas'}</h3>
        {group.fields.map((row, index) => <div className="form-row" key={row.id}>
          <Field label="Name" error={rows.formState.errors[kind]?.[index]?.name?.message}><Input {...rows.register(`${kind}.${index}.name`)} maxLength={300} /></Field>
          <Field label="Description" error={rows.formState.errors[kind]?.[index]?.description?.message}><textarea rows={2} {...rows.register(`${kind}.${index}.description`)} maxLength={4000} /></Field>
          <Button variant="ghost" size="sm" type="button" onClick={() => group.remove(index)}>Remove</Button>
        </div>)}
        <Button variant="outline" type="button" onClick={() => group.append({name: '', description: ''})}>
          {kind === 'segments' ? 'Add segment' : 'Add persona'}
        </Button>
      </section>)}
      <Problem error={save.error} reload={props.reload} />
      <Button type="submit" disabled={save.isPending}>Save audience</Button>
    </form>
  </Dialog>;
}
