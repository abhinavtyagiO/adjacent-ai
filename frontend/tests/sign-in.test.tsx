import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {SignInForm} from '@/features/auth/sign-in-form';

vi.mock('next/navigation', () => ({
  useRouter: () => ({replace: vi.fn(), refresh: vi.fn()}),
}));

afterEach(() => vi.unstubAllGlobals());

describe('SignInForm', () => {
  it('validates empty input before calling the gateway', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<SignInForm configured setup={false} />);
    await userEvent.click(screen.getByRole('button', {name: 'Sign in'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter your workspace passphrase.');
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows server failures next to the sign-in form', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({message: 'The workspace passphrase is incorrect.'}), {status: 401},
    )));
    render(<SignInForm configured setup={false} />);
    await userEvent.type(screen.getByLabelText('Workspace passphrase'), 'incorrect-passphrase');
    await userEvent.click(screen.getByRole('button', {name: 'Sign in'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('The workspace passphrase is incorrect.');
  });

  it('keeps sign-in disabled without local configuration', () => {
    render(<SignInForm configured={false} setup />);
    expect(screen.getByRole('button', {name: 'Connect workspace'})).toBeDisabled();
  });
});
