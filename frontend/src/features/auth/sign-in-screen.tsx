import Link from 'next/link';
import {redirect} from 'next/navigation';
import {Wordmark} from '@/components/layout/wordmark';
import {hasLocalSession, localConfig} from '@/lib/server/session';
import {SignInForm} from './sign-in-form';

export async function SignInScreen({setup = false}: {setup?: boolean}) {
  if (await hasLocalSession()) redirect('/workspace');
  const configured = !!localConfig();

  return <main className="auth-shell">
    <Wordmark href="/sign-in" />
    <div className="auth-grid">
      <section className="auth-editorial" aria-label="Adjacent Studio">
        <div>
          <h1>Good ideas deserve<br className="desktop-break" /> to be seen.</h1>
          <p>Your brand, creative ideas and videos.<br />One thoughtful workspace.</p>
        </div>
        <small>Adjacent / Creative Studio</small>
      </section>
      <section className="auth-access" aria-labelledby="login-heading">
        <h2 id="login-heading">{setup ? 'Connect your workspace' : 'Welcome back'}</h2>
        <p>{setup ? 'Use the passphrase configured for your local workspace.' : 'Sign in to your creative workspace.'}</p>
        {!configured && <p className="auth-note">Local sign-in needs to be enabled by the workspace administrator.</p>}
        <SignInForm key={setup ? 'setup' : 'sign-in'} configured={configured} setup={setup} />
        <Link className="text-link" href={setup ? '/sign-in' : '/sign-up'}>
          {setup ? 'Back to sign in' : 'New to Adjacent? Connect a workspace'}
        </Link>
      </section>
    </div>
  </main>;
}
