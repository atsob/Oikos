import type React from 'react'

/** A link to an outside site, opened in a new tab. The click stops here, so inside a grid row it does not also count
 *  towards the row's double-click-to-edit. */
export default function ExtLink({ href, title, children }: { href: string; title: string; children: React.ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer" title={title} onClick={e => e.stopPropagation()}
    className="text-blue-700 hover:underline">{children}</a>
}
