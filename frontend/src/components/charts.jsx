// Dashboard charts, drawn with plain HTML and SVG: rounded pill columns, hatched quieter series,
// one solid highlight. Marks carry colour; text stays in ink colours. Every chart has a hover/focus
// tooltip and a table view, and every rupee figure comes from the API (rules engine), never summed here.
import { useState } from 'react'
import { Card, CardHeader } from './ui'

// One green family from the theme, deep to pale, plus a single warm coral for "this went wrong".
export const SERIES = '#1a6f50' // brand green (accent-600)
export const SOFT = '#71bd9c' // accent-300
export const PALE = '#bfe0cf' // between accent-100 and accent-200
export const MUTED = '#dde6e1' // the de-emphasised part in an emphasis chart
export const WARN = '#e07a5f' // coral: denials and cuts beyond the wording
export const MODE_COLORS = { basic: '#c4d3cb', rules: SOFT, claimpilot: SERIES }
// Insurer answers carry status meaning; each always shows with its label (legend, tooltip, table).
export const ANSWER_KEYS = [
  { key: 'APPROVE', label: 'Approved', color: SERIES },
  { key: 'PARTIAL_APPROVE', label: 'Part-approved', color: SOFT },
  { key: 'QUERY', label: 'Query', color: PALE },
  { key: 'DENY', label: 'Denied', color: WARN },
]

// ---- Frame: a card with a chart/table switch and one tooltip ---------------------------------

export function ChartCard({ title, subtitle, table, className = '', footer, icon, action, children }) {
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
      <CardHeader title={title} subtitle={subtitle} icon={icon} action={action ?? (table && (
        <div className="flex rounded-full bg-canvas p-1 text-xs font-semibold" role="tablist" aria-label={`${title}: view`}>
          {['chart', 'table'].map((v) => (
            <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}
              className={`rounded-full px-3 py-1 capitalize transition ${view === v ? 'bg-accent-600 text-white shadow-glow' : 'text-ink-muted hover:text-ink'}`}>{v}</button>
          ))}
        </div>
      ))} />
      <div data-chart className="relative px-6 pb-6">
        {view === 'table' && table ? <DataTable {...table} /> : children({ show, hide })}
        {tip && view === 'chart' && (
          <div className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full animate-fade-in whitespace-nowrap rounded-full bg-ink px-3 py-1.5 text-xs font-semibold text-white shadow-pop"
            style={{ left: tip.x, top: tip.y - 8 }}>{tip.content}</div>
        )}
        {footer && <div className="mt-4 text-xs leading-relaxed text-ink-faint">{footer}</div>}
      </div>
    </Card>
  )
}

function DataTable({ columns, rows }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-line">
      <table className="w-full text-[13px]">
        <thead><tr className="bg-canvas text-left text-ink-muted">{columns.map((c, i) => <th key={c} className={`px-3 py-2 font-semibold ${i ? 'text-right' : ''}`}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-t border-line">
              {row.map((cell, i) => <td key={i} className={`px-3 py-2 ${i ? 'text-right tabular' : 'text-ink-soft'}`}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  const top = data.reduce((best, d, i) => (d.value > (data[best]?.value ?? -1) ? i : best), 0)
  return (
    <ul className="space-y-4">
      {data.map((d, i) => {
        const lead = i === top && d.value > 0
        return (
          <li key={d.label} className="text-[13px]">
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <span className={`${labelWidth} min-w-0 flex-1 truncate font-medium text-ink-soft`} title={d.label}>{d.label}</span>
              <span className={`shrink-0 font-bold tabular ${d.value ? 'text-ink' : 'text-ink-faint'}`}>{format(d.value)}</span>
            </div>
            <div className="stripes-ink relative h-3 rounded-full bg-canvas">
              {d.value > 0 && (
                <div {...hover(show, hide, `${d.label}: ${format(d.value)}`)}
                  className={`h-full origin-left animate-grow-x rounded-full outline-none transition hover:brightness-110 focus-visible:ring-2 focus-visible:ring-accent-500 ${lead ? 'shadow-glow' : ''}`}
                  style={{ width: `${(d.value / max) * 100}%`, minWidth: 12, background: lead ? color : SOFT, animationDelay: `${i * 60}ms` }} />
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// ---- Pill columns: each category is a rounded track filled from the bottom, the biggest solid ---

export function PillColumns({ data, show, hide, format = (v) => v, height = 200, color = SERIES }) {
  const max = Math.max(1, ...data.map((d) => d.value))
  const top = data.reduce((best, d, i) => (d.value > (data[best]?.value ?? -1) ? i : best), 0)
  const track = height - 30 // room for the value above each column
  return (
    <div>
      <div className="flex items-end justify-around gap-2" style={{ height }}>
        {data.map((d, i) => {
          const solid = i === top && d.value > 0
          const fill = d.value ? Math.max(0.16, d.value / max) : 0
          return (
            <div key={d.label} className="flex h-full w-full max-w-[48px] flex-col items-center justify-end gap-2">
              {solid
                ? <span className="animate-fade-in rounded-full bg-ink px-2 py-0.5 text-[11px] font-bold tabular text-white">{format(d.value)}</span>
                : <span className={`text-[11px] font-bold tabular ${d.value ? 'text-ink-soft' : 'text-ink-faint'}`}>{format(d.value)}</span>}
              <div {...hover(show, hide, `${d.label}: ${format(d.value)}`)}
                className="stripes-ink relative w-full overflow-hidden rounded-full bg-canvas outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
                style={{ height: track }}>
                {fill > 0 && (
                  <div className={`absolute inset-x-0 bottom-0 origin-bottom animate-grow rounded-full transition hover:brightness-110 ${solid ? 'shadow-glow' : ''}`}
                    style={{ height: `${fill * 100}%`, background: solid ? color : SOFT, animationDelay: `${i * 70}ms` }} />
                )}
              </div>
            </div>
          )
        })}
      </div>
      <div className="mt-3 flex justify-around gap-2">
        {data.map((d) => <span key={d.label} className="w-full max-w-[80px] text-center text-[11px] font-semibold leading-tight text-ink-muted" title={d.label}>{d.label}</span>)}
      </div>
    </div>
  )
}

// ---- Donut: a whole split into a few labelled parts, a figure in the middle --------------------

export function Donut({ parts, show, hide, format = (v) => v, center, caption, size = 176 }) {
  const sum = parts.reduce((s, p) => s + p.value, 0) // shares for drawing only; totals come from the API
  const r = 40
  const c = 2 * Math.PI * r
  const shown = parts.filter((p) => p.value)
  const gap = shown.length > 1 ? 4 : 0
  // a tiny share still gets a readable arc; the arcs are then scaled back to fill the ring
  const raw = shown.map((p) => Math.max((p.value / (sum || 1)) * c, 22))
  const scale = c / (raw.reduce((a, b) => a + b, 0) || 1)
  const arcs = raw.map((len) => len * scale)
  let offset = 0
  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="relative shrink-0" style={{ width: size, height: size }}>
        <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90">
          <circle cx="50" cy="50" r={r} fill="none" stroke="#edf2ef" strokeWidth="14" />
          {sum > 0 && shown.map((p, i) => {
            const len = arcs[i]
            const seg = (
              <circle key={p.label} cx="50" cy="50" r={r} fill="none" stroke={p.color} strokeWidth="14" strokeLinecap="round"
                strokeDasharray={`${Math.max(0.5, len - gap - 7)} ${c}`} strokeDashoffset={-(offset + 3.5)} {...hover(show, hide, `${p.label}: ${format(p.value)}`)}
                className="cursor-default outline-none transition-opacity hover:opacity-80" />
            )
            offset += len
            return seg
          })}
          <circle cx="50" cy="50" r="29" fill="#f6f9f7" />
        </svg>
        <div className="absolute inset-0 grid place-items-center text-center">
          <div>
            <p className="text-lg font-bold tracking-tight tabular">{center}</p>
            {caption && <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{caption}</p>}
          </div>
        </div>
      </div>
      <ul className="min-w-[180px] flex-1 space-y-2">
        {parts.map((p) => (
          <li key={p.label} className="flex items-center justify-between gap-3 rounded-2xl bg-canvas/70 px-3 py-2 text-[13px]">
            <span className="flex items-center gap-2 text-ink-soft"><span className="h-3 w-3 shrink-0 rounded-full ring-2 ring-white" style={{ background: p.color }} />{p.label}</span>
            <span className="font-bold tabular">{format(p.value)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ---- Stacked horizontal bars: part-to-whole per row ------------------------------------------

export function Legend({ keys }) {
  return (
    <div className="mb-4 flex flex-wrap gap-1.5 text-xs font-semibold text-ink-soft">
      {keys.map((k) => <span key={k.key} className="flex items-center gap-1.5 rounded-full bg-canvas px-2.5 py-1"><span className="h-2.5 w-2.5 rounded-full" style={{ background: k.color }} />{k.label}</span>)}
    </div>
  )
}

export function StackedBars({ rows, keys, show, hide, unit = '' }) {
  return (
    <>
      <Legend keys={keys} />
      <ul className="space-y-3">
        {rows.map((row, r) => {
          const sum = keys.reduce((s, k) => s + (row[k.key] ?? 0), 0) // counts of answers, not rupees
          return (
            <li key={row.label} className="flex items-center gap-3 text-[13px]">
              <span className="w-24 shrink-0 font-semibold text-ink-soft">{row.label}</span>
              <div className="flex h-7 min-w-0 flex-1 origin-left animate-grow-x gap-1 rounded-full bg-canvas p-1" style={{ animationDelay: `${r * 60}ms` }}>
                {keys.filter((k) => row[k.key]).map((k) => {
                  const share = row[k.key] / sum
                  return (
                    <div key={k.key} {...hover(show, hide, `${row.label} · ${k.label}: ${row[k.key]}${unit}`)}
                      className="grid h-full place-items-center rounded-full text-[11px] font-bold outline-none transition hover:brightness-105 focus-visible:ring-2 focus-visible:ring-accent-500"
                      style={{ width: `${share * 100}%`, background: k.color, color: k.color === SERIES || k.color === WARN ? '#fff' : '#14201c' }}>
                      {share >= 0.12 ? row[k.key] : ''}
                    </div>
                  )
                })}
              </div>
              <span className="w-8 shrink-0 text-right font-bold tabular text-ink">{sum}</span>
            </li>
          )
        })}
      </ul>
    </>
  )
}

// ---- Columns: an ordered distribution (e.g. risk bands) --------------------------------------

export function Columns({ data, show, hide, color = SERIES, height = 180 }) {
  return <PillColumns data={data} show={show} hide={hide} color={color} height={height} format={(v) => String(v)} />
}

// ---- Emphasis bar: one part of a whole is the point ------------------------------------------

export function SplitBar({ parts, show, hide, format }) {
  const sum = parts.reduce((s, p) => s + p.value, 0) || 1 // shares for drawing only; totals come from the API
  return (
    <div>
      <div className="flex h-5 origin-left animate-grow-x gap-1 rounded-full bg-canvas p-1">
        {parts.filter((p) => p.value).map((p) => (
          <div key={p.label} {...hover(show, hide, `${p.label}: ${format(p.value)}`)} className="h-full rounded-full outline-none" style={{ width: `${(p.value / sum) * 100}%`, background: p.color }} />
        ))}
      </div>
      <div className="mt-4 space-y-2">
        {parts.map((p) => (
          <div key={p.label} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="flex items-center gap-2 text-ink-soft"><span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: p.color }} />{p.label}</span>
            <span className="tabular font-bold">{format(p.value)}</span>
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
    <div className="rounded-2xl border border-line/70 bg-canvas/60 p-4">
      <p className="mb-3 text-[13px] font-bold text-ink-soft">{metric.label}<span className="font-medium text-ink-faint"> · {metric.better} is better{metric.of ? ` · of ${metric.of}` : ''}</span></p>
      <ul className="space-y-2">
        {modes.map((m) => (
          <li key={m} className="flex items-center gap-3 text-[13px]">
            <span className="w-20 shrink-0 font-medium text-ink-muted">{m}</span>
            <div className="h-3 min-w-0 flex-1 rounded-full bg-white">
              <div {...hover(show, hide, `${m}: ${format(metric.values[m])}`)} className="h-full origin-left animate-grow-x rounded-full outline-none"
                style={{ width: `${((metric.values[m] ?? 0) / max) * 100}%`, minWidth: metric.values[m] ? 6 : 0, background: MODE_COLORS[m] }} />
            </div>
            <span className="w-14 shrink-0 text-right font-semibold tabular text-ink">{format(metric.values[m])}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
