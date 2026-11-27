import Link from 'next/link';

export function Wordmark({href = '/workspace'}: {href?: string}) {
  return <Link className="wordmark" href={href} aria-label="Adjacent AI home">
    <span aria-hidden="true">A</span>
    adjacent.
  </Link>;
}
