import { createContext } from 'react'

/** On a phone the page's title lives in the app bar at the top, next to the menu button. A page's PageHeader reports its title here. */
export const PhoneTitleContext = createContext<(title: string) => void>(() => {})
