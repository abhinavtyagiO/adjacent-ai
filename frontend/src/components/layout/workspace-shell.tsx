'use client';

import {ChevronDown, Menu, X} from 'lucide-react';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useState, type ReactNode} from 'react';
import type {BrandRecord} from '@/api/generated/workspace';
import {Button} from '@/components/ui/button';
import {brandName, workspaceHref} from '@/features/brands/labels';
import {Wordmark} from './wordmark';

export const sectionNames = ['overview', 'brand', 'campaigns', 'videos', 'media', 'settings'] as const;
export type WorkspaceSection = typeof sectionNames[number];

const nav: {key: WorkspaceSection; name: string}[] = [
  {key: 'overview', name: 'Overview'},
  {key: 'brand', name: 'Brand'},
  {key: 'campaigns', name: 'Campaigns'},
  {key: 'videos', name: 'Videos'},
  {key: 'media', name: 'Media library'},
  {key: 'settings', name: 'Settings'},
];

export function WorkspaceShell({selected, brands, section, children}: {
  selected?: BrandRecord;
  brands: BrandRecord[];
  section: WorkspaceSection;
  children: ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  return <div className="workspace-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="workspace-nav">
      <div className="workspace-nav-head">
        <Wordmark />
        <Button className="nav-toggle" size="icon" variant="ghost" aria-controls="workspace-links"
          aria-expanded={open} aria-label={open ? 'Close navigation' : 'Open navigation'}
          onClick={() => setOpen(value => !value)}>
          {open ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
        </Button>
      </div>
      <div id="workspace-links" className="workspace-links" data-open={open}>
        {brands.length ? <div className="brand-switcher">
          <label htmlFor="workspace-brand">Switch brand</label>
          <select id="workspace-brand" value={selected?.id ?? ''} onChange={event => {
            if (!event.currentTarget.value) return;
            router.push(event.currentTarget.value === 'new' ? '/workspace/new-brand' : workspaceHref(event.currentTarget.value));
            setOpen(false);
          }}>
            {!selected && <option value="" disabled>Choose a brand</option>}
            {brands.map(brand => <option key={brand.id} value={brand.id}>{brandName(brand)}</option>)}
            <option value="new">Add a brand…</option>
          </select>
          <ChevronDown size={16} aria-hidden="true" />
        </div> : <div className="brand-switcher brand-switcher-empty"><span>My workspace</span><small>No brands available</small></div>}
        <nav aria-label="Workspace navigation">
          {nav.map(({key, name}) => {
            const current = section === key;
            const href = key === 'settings'
              ? selected ? workspaceHref(selected.id, 'settings') : '/workspace/settings'
              : selected
                ? workspaceHref(selected.id, key === 'overview' ? undefined : key)
                : undefined;
            return href
              ? <Link key={key} href={href} className="nav-link" onClick={() => setOpen(false)}
                  aria-current={current ? 'page' : undefined}>{name}</Link>
              : <span key={key} className="nav-link nav-link--unavailable" aria-disabled="true"
                  title='Select a brand first'>{name}</span>;
          })}
        </nav>
        <Link className="workspace-account" href={selected ? workspaceHref(selected.id, 'settings') : '/workspace/settings'}>
          My workspace <span aria-hidden="true">/</span> Account
        </Link>
      </div>
    </aside>
    <main id="main-content" tabIndex={-1} className="workspace-main">{children}</main>
  </div>;
}
