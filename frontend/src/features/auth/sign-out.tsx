'use client';

import {useQueryClient} from '@tanstack/react-query';
import {useRouter} from 'next/navigation';
import {useState} from 'react';
import {Button} from '@/components/ui/button';

export function SignOut() {
  const router = useRouter();
  const query = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function signOut() {
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch('/api/auth/session', {method: 'DELETE'});
      if (!response.ok) throw new Error('Sign out failed');
      query.clear();
      router.replace('/sign-in');
      router.refresh();
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return <>
    <Button variant="outline" disabled={busy} onClick={() => void signOut()}>{busy ? 'Signing out…' : 'Sign out'}</Button>
    {failed && <p className="form-error" role="alert">Could not sign out. Try again.</p>}
  </>;
}
