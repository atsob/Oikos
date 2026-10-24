/* eslint-disable @typescript-eslint/no-explicit-any */
import PlotlyReact from 'react-plotly.js'
import { usePhone } from '@/lib/useMedia'

// react-plotly.js is CommonJS: depending on the bundler the component is the default export or its .default
const Base: React.ComponentType<any> = (PlotlyReact as any).default ?? PlotlyReact

/** The app's Plotly chart. On a phone it drops the desktop-sized side margins, uses a smaller font, hides the toolbar and turns drag-to-zoom off —
 *  with it on, a finger that starts on a chart zooms it instead of scrolling the page. Tapping still shows the tooltip. */
export default function Plot(props: any) {
  const phone = usePhone()
  if (!phone) return <Base {...props} />
  const m = props.layout?.margin ?? {}
  const layout = {
    ...props.layout,
    margin: { ...m, l: Math.min(m.l ?? 60, 44), r: Math.min(m.r ?? 20, 14) },
    dragmode: props.layout?.dragmode ?? false,
    font: { size: 11, ...(props.layout?.font ?? {}) },
  }
  const config = { ...props.config, displayModeBar: false, scrollZoom: false }
  return <Base {...props} layout={layout} config={config} />
}
