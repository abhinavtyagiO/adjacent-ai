import type {ReactNode} from 'react';

export function PageHead({section, title, subtitle, action}: {
  section: string;
  title: string;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return <>
    <p className="breadcrumbs">Workspace <span aria-hidden="true">/</span> {section}</p>
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action}
    </header>
  </>;
}
