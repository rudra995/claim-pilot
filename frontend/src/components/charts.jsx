// Small, quiet charts for the dashboard, drawn with plain HTML and SVG.
// Marks carry colour; text stays in ink colours. Every chart has a hover/focus tooltip and a table view.
import { useState } from 'react'
import { Card, CardHeader } from './ui'

// Colours, checked with the dataviz palette validator against the white card surface.
export const SERIES = '#2f7d6d' // one series: the brand accent (4.9:1 on white)
export const MUTED = '#c3c2b7' // the de-emphasised part in an emphasis chart
export const MODE_COLORS = { basic: '#2a78d6', rules: '#eb6834', claimpilot: '#1baf7a' } // categorical slots 1-3
// Insurer answers carry status meaning; each always shows with its label (legend, tooltip, table).
export const ANSWER_KEYS = [
  { key: 'APPROVE', label: 'Approved', color: '#0ca30c' },
  { key: 'PARTIAL_APPROVE', label: 'Part-approved', color: '#fab219' },
  { key: 'QUERY', label: 'Query', color: '#2a78d6' },
  { key: 'DENY', label: 'Denied', color: '#d03b3b' },
]

// ---- Frame: a card with a chart/table switch and one tooltip ---------------------------------

export function ChartCard({ title, subtitle, table, className = '', footer, children }) {
  const [view, setView] = useState('chart')
  const [tip, setTip] = useState(null)
  const show = (event, content) => {
    const box = event.currentTarget.closest('[data-chart]').getBoundingClientRect()
    const mark = event.currentTarget.getBoundingClientRect()
    setTip({ content, x: mark.left - box.left + mark.width / 2, y: mark.top - box.top })
  }
  const hide = () => setTip(null)
  return (
    <Card className={className}>
      <CardHeader title={title} subtitle={subtitle} action={table && (
        <div className="flex rounded-md border border-line p-0.5 text-xs" role="tablist" aria-label={`${title}: view`}>
          {['chart', 'table'].map((v) => (
            <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
              className={`rounded px-2 py-0.5 capitalize transition ${view === v ? 'bg-zinc-100 font-medium text-ink' : 'text-ink-muted hover:text-ink'}`}>{v}</button>
          ))}
        </div>
      )} />
      <div data-chart className="relative px-5 pb-5">
        {view === 'table' && table ? <DataTable {...table} /> : children({ show, hide })}
        {tip && view === 'chart' && (
          <div className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md bg-ink px-2.5 py-1.5 text-xs text-white shadow-pop"
            style={{ left: tip.x, top: tip.y - 6 }}>{tip.content}</div>
        )}
        {footer && <div className="mt-4 text-xs text-ink-faint">{footer}</div>}
      </div>
    </Card>
  )
}

function DataTable({ columns, rows }) {
  return (
    <table className="w-full text-[13px]">
      <thead><tr className="border-b border-line text-left text-ink-muted">{columns.map((c, i) => <th key={c} className={`py-1.5 font-medium ${i ? 'text-right' : ''}`}>{c}</th>)}</tr></thead>
      <tbody>
        {rows.map((row, r) => (
          <tr key={r} className="border-b border-line/60 last:border-0">
            {row.map((cell, i) => <td key={i} className={`py-1.5 ${i ? 'text-right tabular' : 'text-ink-soft'}`}>{cell}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Hover and keyboard focus both show the same tooltip.
const hover = (show, hide, content) => ({
  tabIndex: 0, 'aria-label': typeof content === 'string' ? content : undefined,
  onPointerEnter: (e) => show(e, content), onPointerLeave: hide, onFocus: (e) => show(e, content), onBlur: hide,
})

// ---- Horizontal bars: one series, value at the tip -------------------------------------------

export function BarList({ data, format = (v) => v, color = SERIES, show, hide, labelWidth = 'w-28 sm:w-36' }) {
  const max = Math.max(1, ...data.map((d) => d.value))
  return (
    <ul className="space-y-2.5">
      {data.map((d) => (
        <li key={d.label} className="flex items-center gap-3 text-[13px]">
          <span className={`${labelWidth} shrink-0 truncate text-ink-soft`} title={d.label}>{d.label}</span>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div {...hover(show, hide, `${d.label}: ${format(d.value)}`)} className="h-3.5 rounded-r-[4px] outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-accent-500"
              style={{ width: `${(d.value / max) * 100}%`, minWidth: d.value ? 3 : 0, background: color }} />
            <span className="shrink-0 tabular text-ink-muted">{format(d.value)}</span>
          </div>
        </li>
      ))}
    </ul>
  )
}

// ---- Stacked horizontal bars: part-to-whole per row ------------------------------------------

export function Legend({ keys }) {
  return (
    <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
      {keys.map((k) => <span key={k.key} className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: k.color }} />{k.label}</span>)}
    </div>
  )
}

export function StackedBars({ rows, keys, show, hide, unit = '' }) {
  return (
    <>
      <Legend keys={keys} />
      <ul className="space-y-3">
        {rows.map((row) => {
          const sum = keys.reduce((s, k) => s + (row[k.key] ?? 0), 0) // counts of answers, not rupees
          return (
            <li key={row.label} className="flex items-center gap-3 text-[13px]">
              <span className="w-24 shrink-0 text-ink-soft">{row.label}</span>
              <div className="flex h-3.5 min-w-0 flex-1 gap-[2px] overflow-hidden rounded-[4px]">
                {keys.filter((k) => row[k.key]).map((k) => (
                  <div key={k.key} {...hover(show, hide, `${row.label} · ${k.label}: ${row[k.key]}${unit}`)} className="h-full outline-none transition-opacity hover:opacity-80 focus-visible:opacity-70"
                    style={{ width: `${(row[k.key] / sum) * 100}%`, background: k.color }} />
                ))}
              </div>
              <span className="w-8 shrink-0 text-right tabular text-ink-muted">{sum}</span>
            </li>
          )
        })}
      </ul>
    </>
  )
}

// ---- Columns: an ordered distribution (e.g. risk bands) --------------------------------------

export function Columns({ data, show, hide, color = SERIES, height = 120 }) {
  const max = Math.max(1, ...data.map((d) => d.value))
  return (
    <div>
      <div className="relative flex items-end gap-3 border-b border-[#c3c2b7]" style={{ height }}>
        {[0.5, 1].map((f) => <div key={f} className="pointer-events-none absolute inset-x-0 border-t border-[#e1e0d9]" style={{ bottom: f * (height - 18) }} />)}
        {data.map((d) => (
          <div key={d.label} className="relative flex flex-1 flex-col items-center justify-end" style={{ height: '100%' }}>
            {d.value > 0 && <span className="mb-1 text-xs tabular text-ink-muted">{d.value}</span>}
            <div {...hover(show, hide, `${d.label}: ${d.value} claim${d.value === 1 ? '' : 's'}`)} className="w-full max-w-[24px] rounded-t-[4px] outline-none transition-opacity hover:opacity-80 focus-visible:ring-2 focus-visible:ring-accent-500"
              style={{ height: `${(d.value / max) * (height - 18)}px`, minHeight: d.value ? 3 : 0, background: color }} />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex gap-3">{data.map((d) => <span key={d.label} className="flex-1 text-center text-[11px] text-ink-muted">{d.label}</span>)}</div>
    </div>
  )
}

// ---- Emphasis bar: one part of a whole is the point ------------------------------------------

export function SplitBar({ parts, show, hide, format }) {
  const sum = parts.reduce((s, p) => s + p.value, 0) || 1 // shares for drawing only; totals come from the API
  return (
    <div>
      <div className="flex h-4 gap-[2px] overflow-hidden rounded-[4px] bg-zinc-100">
        {parts.filter((p) => p.value).map((p) => (
          <div key={p.label} {...hover(show, hide, `${p.label}: ${format(p.value)}`)} className="h-full outline-none" style={{ width: `${(p.value / sum) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="mt-3 space-y-1.5">
        {parts.map((p) => (
          <div key={p.label} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="flex items-center gap-2 text-ink-soft"><span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: p.color }} />{p.label}</span>
            <span className="tabular font-medium">{format(p.value)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---- Small multiples: the same three modes, one small chart per metric -----------------------

export function ModeBars({ metric, modes, show, hide, format }) {
  // a share is drawn against 100%, so a two-point gap looks like two points
  const max = metric.unit === 'share' ? 1 : Math.max(1, ...modes.map((m) => metric.values[m] ?? 0))
  return (
    <div>
      <p className="mb-2 text-[13px] font-medium text-ink-soft">{metric.label}<span className="font-normal text-ink-faint"> · {metric.better} is better{metric.of ? ` · of ${metric.of}` : ''}</span></p>
      <ul className="space-y-1.5">
        {modes.map((m) => (
          <li key={m} className="flex items-center gap-3 text-[13px]">
            <span className="w-20 shrink-0 text-ink-muted">{m}</span>
            <div className="flex min-w-0 flex-1 items-center gap-2">
              <div {...hover(show, hide, `${m}: ${format(metric.values[m])}`)} className="h-3 rounded-r-[4px] outline-none"
                style={{ width: `${((metric.values[m] ?? 0) / max) * 100}%`, minWidth: metric.values[m] ? 3 : 0, background: MODE_COLORS[m] }} />
              <span className="shrink-0 tabular text-ink-muted">{format(metric.values[m])}</span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
