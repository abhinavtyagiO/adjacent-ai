'use client';

import {zodResolver} from '@hookform/resolvers/zod';
import {useRouter} from 'next/navigation';
import {useForm} from 'react-hook-form';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {loginSchema, type LoginValues} from './schema';

export function SignInForm({configured, setup}: {configured: boolean; setup: boolean}) {
  const router = useRouter();
  const {register, handleSubmit, reset, setError, formState: {errors, isSubmitting}} = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: {passphrase: ''},
  });

  async function signIn(values: LoginValues) {
    try {
      const response = await fetch('/api/auth/session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(values),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        setError('root.server', {message: body?.message ?? 'Could not sign in. Try again.'});
        return;
      }
      reset();
      router.replace('/workspace');
      router.refresh();
    } catch {
      setError('root.server', {message: 'Could not connect to the workspace. Try again.'});
    }
  }

  return <form className="auth-form" onSubmit={handleSubmit(signIn)} noValidate>
    <div className="grid gap-2.5">
      <Label htmlFor="passphrase">Workspace passphrase</Label>
      <Input id="passphrase" type="password" autoComplete="current-password"
        placeholder="Enter your passphrase" disabled={!configured || isSubmitting}
        aria-invalid={!!errors.passphrase} aria-describedby={errors.passphrase ? 'login-error' : undefined}
        {...register('passphrase')} />
      {errors.passphrase && <p id="login-error" className="text-sm text-destructive" role="alert">{errors.passphrase.message}</p>}
    </div>
    {errors.root?.server && <p className="form-error" role="alert">{errors.root.server.message}</p>}
    <Button type="submit" disabled={!configured || isSubmitting}>
      {isSubmitting ? 'Connecting…' : setup ? 'Connect workspace' : 'Sign in'}
    </Button>
  </form>;
}
