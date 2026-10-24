// Design system: formatting, plain names, and the small pieces every screen is built from.
import { createContext, useContext, useEffect, useRef, useState } from 'react'

// ---- Formatting ------------------------------------------------------------------------------

const rupees = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
export const money = (amount) => (amount === null || amount === undefined ? '—' : rupees.format(amount))
export const pct = (value) => (value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`)
export const label = (slug) =>
  slug ? String(slug).replaceAll('_', ' ').toLowerCase().replace(/\bpreauth\b/, 'pre-auth').replace(/^\w/, (c) => c.toUpperCase()) : ''

const parseTime = (createdAt) => new Date(`${createdAt.replace(' ', 'T')}${createdAt.endsWith('Z') ? '' : 'Z'}`)
export const timeOf = (createdAt) => (createdAt ? parseTime(createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '')
export function ago(createdAt) {
  if (!createdAt) return ''
  const seconds = Math.max(0, (Date.now() - parseTime(createdAt).getTime()) / 1000)
  if (seconds < 45) return 'just now'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`
  return `${Math.round(seconds / 86400)} d ago`
}

// ---- Plain names -----------------------------------------------------------------------------

const PROCEDURES = {
  laparoscopic_appendectomy: 'Appendix removal (laparoscopic)',
  total_knee_replacement: 'Total knee replacement',
  total_hip_replacement: 'Total hip replacement',
  cataract_surgery: 'Cataract surgery',
  coronary_angioplasty: 'Coronary angioplasty',
  caesarean_section: 'Caesarean section',
  normal_delivery: 'Normal delivery',
  hernia_repair: 'Hernia repair',
  laparoscopic_cholecystectomy: 'Gallbladder removal (laparoscopic)',
  hysterectomy: 'Hysterectomy',
  tonsillectomy: 'Tonsillectomy',
  lithotripsy: 'Kidney stone removal',
  turp: 'Prostate surgery (TURP)',
  dengue_management: 'Dengue care',
  pneumonia_management: 'Pneumonia care',
  gastroenteritis_management: 'Gastroenteritis care',
  typhoid_management: 'Typhoid care',
  acute_mi_management: 'Heart attack care',
  stroke_management: 'Stroke care',
  femur_fracture_fixation: 'Femur fracture fixation',
  acl_reconstruction: 'ACL reconstruction',
  spinal_fusion: 'Spinal fusion',
  cabg: 'Heart bypass surgery (CABG)',
  aki_dialysis: 'Kidney dialysis',
  sinus_surgery: 'Sinus surgery',
}
export const procedureName = (slug) => PROCEDURES[slug] ?? label(slug)

const ROOMS = {
  general_ward: 'General ward', twin_sharing: 'Twin sharing', single_standard: 'Single standard',
  single_ac: 'Single AC', single_deluxe: 'Single deluxe', suite: 'Suite',
}
export const roomName = (slug) => ROOMS[slug] ?? label(slug)

const AGENTS = {
  orchestrator: 'Orchestrator', document_reader: 'Document reader', clinical_coder: 'Clinical coder',
  policy: 'Policy checker', cost_estimator: 'Cost estimator', preauth: 'Pre-auth writer', denial_risk: 'Risk checker',
  query_responder: 'Query responder', appeal: 'Appeal writer', enhancement: 'Bill watcher', discharge: 'Discharge writer',
  reconciliation: 'Reconciler', patient: 'Patient advisor', tpa_medical_reviewer: 'Medical reviewer',
  tpa_fraud_screen: 'Fraud screen', tpa_adjudicator: 'Adjudicator', tpa_query_writer: 'Query writer',
  hospital: 'Hospital', insurer: 'Insurer', api: 'System',
}
export const agentName = (slug) => AGENTS[slug] ?? (slug?.startsWith('human:') ? slug.slice(6) : label(slug))

// The claim's state in a few plain words, and whose move it is.
export const STATUS = {
  ADMITTED: { name: 'Preparing', tone: 'neutral' },
  PREAUTH_DRAFT: { name: 'Preparing', tone: 'neutral' },
  RISK_CHECK: { name: 'Preparing', tone: 'neutral' },
  AWAITING_HUMAN: { name: 'Needs you', tone: 'attention' },
  SUBMITTED: { name: 'With insurer', tone: 'info' },
  QUERY_RAISED: { name: 'Insurer query', tone: 'info' },
  APPROVED: { name: 'Approved', tone: 'good' },
  PARTIALLY_APPROVED: { name: 'Part approved', tone: 'good' },
  DENIED: { name: 'Denied', tone: 'bad' },
  IN_TREATMENT: { name: 'In treatment', tone: 'good' },
  DISCHARGE_DRAFT: { name: 'Discharging', tone: 'neutral' },
  FINAL_APPROVED: { name: 'Awaiting payment', tone: 'info' },
  SETTLED: { name: 'Reconciling', tone: 'neutral' },
  CLOSED: { name: 'Closed', tone: 'muted' },
}

// ---- Badges and status -------------------------------------------------------------------------

const TONES = {
  neutral: 'bg-zinc-100 text-zinc-700',
  muted: 'bg-zinc-100 text-zinc-500',
  attention: 'bg-amber-100/70 text-amber-800',
  info: 'bg-sky-100/70 text-sky-800',
  good: 'bg-accent-50 text-accent-700',
  bad: 'bg-rose-100/70 text-rose-700',
}
const DOTS = {
  neutral: 'bg-zinc-400', muted: 'bg-zinc-300', attention: 'bg-amber-500', info: 'bg-sky-500', good: 'bg-accent-500', bad: 'bg-rose-500',
}

export function Badge({ tone = 'neutral', dot, pulse, children, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ${TONES[tone]} ${className}`}>
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${DOTS[tone]} ${pulse ? 'animate-breathe' : ''}`} />}
      {children}
    </span>
  )
}

export function Status({ state, busy }) {
  const meta = STATUS[state] ?? { name: label(state), tone: 'neutral' }
  if (busy) return <Badge tone="neutral" dot pulse>Working</Badge>
  return <Badge tone={meta.tone} dot pulse={meta.tone === 'attention'}>{meta.name}</Badge>
}

export const riskTone = (risk) => (risk >= 0.5 ? 'bad' : risk >= 0.2 ? 'attention' : 'good')
export const riskWord = (risk) => (risk >= 0.5 ? 'High' : risk >= 0.2 ? 'Medium' : 'Low')

// ---- Layout pieces ---------------------------------------------------------------------------

export function Card({ children, className = '', ...props }) {
  return <section className={`rounded-xl border border-line bg-white shadow-card ${className}`} {...props}>{children}</section>
}

export function CardHeader({ title, subtitle, action }) {
  return (
    <header className="flex items-start justify-between gap-4 px-5 pb-3 pt-4">
      <div>
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="mt-0.5 text-[13px] text-ink-muted">{subtitle}</p>}
      </div>
      {action}
    </header>
  )
}

export function PageHeader({ title, subtitle, action }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-muted">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

export function Button({ variant = 'secondary', size = 'md', icon, iconRight, loading, className = '', children, disabled, ...props }) {
  const styles = {
    primary: 'bg-ink text-white hover:bg-zinc-800 disabled:bg-zinc-300',
    accent: 'bg-accent-600 text-white hover:bg-accent-700 disabled:bg-accent-100',
    secondary: 'border border-line bg-white text-ink shadow-card hover:bg-zinc-50 disabled:text-ink-faint',
    ghost: 'text-ink-soft hover:bg-zinc-100',
    danger: 'border border-line bg-white text-rose-700 shadow-card hover:bg-rose-50 disabled:text-rose-300',
  }
  const sizes = { sm: 'h-8 px-3 text-[13px]', md: 'h-9 px-3.5 text-sm', lg: 'h-11 px-5 text-[15px]' }
  return (
    <button disabled={disabled || loading}
      className={`inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-all duration-150 active:scale-[0.98] disabled:cursor-not-allowed ${styles[variant]} ${sizes[size]} ${className}`} {...props}>
      {loading ? <Spinner className="h-3.5 w-3.5" /> : icon && <Icon name={icon} className="h-4 w-4" />}
      {children}
      {iconRight && <Icon name={iconRight} className="h-4 w-4 opacity-60" />}
    </button>
  )
}

export function Avatar({ name, size = 'md', className = '' }) {
  const initials = (name ?? '?').split(/[\s-]+/).filter(Boolean).map((part) => part[0]).slice(0, 2).join('').toUpperCase()
  const palette = ['bg-[#e8e4dc] text-[#5c5244]', 'bg-[#dde7e4] text-[#2f5a51]', 'bg-[#e4e1ea] text-[#4b4463]', 'bg-[#ece2dc] text-[#6b4a3a]', 'bg-[#dfe5ec] text-[#3c4f63]']
  const colour = palette[[...(name ?? '')].reduce((sum, c) => sum + c.charCodeAt(0), 0) % palette.length]
  const sizes = { xs: 'h-6 w-6 text-[10px]', sm: 'h-8 w-8 text-[11px]', md: 'h-10 w-10 text-xs', lg: 'h-14 w-14 text-base' }
  return <span className={`grid shrink-0 place-items-center rounded-full font-semibold ${colour} ${sizes[size]} ${className}`}>{initials}</span>
}

export function EmptyState({ icon = 'inbox', title, children, action }) {
  return (
    <div className="flex animate-fade-up flex-col items-center justify-center rounded-xl border border-dashed border-zinc-300 bg-white/60 px-6 py-16 text-center">
      <span className="grid h-11 w-11 place-items-center rounded-full bg-zinc-100 text-ink-muted"><Icon name={icon} className="h-5 w-5" /></span>
      <p className="mt-4 text-[15px] font-medium">{title}</p>
      {children && <div className="mt-1 max-w-sm text-sm text-ink-muted">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function ErrorNote({ error }) {
  if (!error) return null
  return (
    <div className="flex animate-fade-in items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-800">
      <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{error}</span>
    </div>
  )
}

export function Spinner({ className = 'h-4 w-4' }) {
  return <span className={`inline-block animate-spin rounded-full border-[1.5px] border-black/10 border-t-current ${className}`} />
}

export function Skeleton({ className = 'h-4 w-full' }) {
  return <div className={`skeleton ${className}`} />
}

// Counts up to a number when it first appears or changes.
export function CountUp({ value, format = (v) => v }) {
  const [shown, setShown] = useState(value ?? 0)
  const from = useRef(0)
  useEffect(() => {
    if (value === null || value === undefined) return undefined
    const start = performance.now()
    const begin = from.current
    let frame
    const tick = (now) => {
      const t = Math.min(1, (now - start) / 700)
      const eased = 1 - (1 - t) ** 3
      setShown(begin + (value - begin) * eased)
      if (t < 1) frame = requestAnimationFrame(tick)
      else from.current = value
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [value])
  if (value === null || value === undefined) return '—'
  return format(Math.round(shown))
}

// Staggered entrance for lists: pass the item's index.
export const stagger = (i, step = 35) => ({ animationDelay: `${Math.min(i, 12) * step}ms` })

// ---- Icons (1.6px strokes on a 24px grid) ----------------------------------------------------

const PATHS = {
  home: 'M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.3a3.5 3.5 0 0 1 0 6.4M18 14.5a6.5 6.5 0 0 1 3.5 5.5',
  tasks: 'M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2',
  building: 'M4 20V6a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v14M14 10h5a1 1 0 0 1 1 1v9M3 20h18M7.5 9h3M7.5 12.5h3M7.5 16h3',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-3.5-3.5',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  x: 'M6 6l12 12M18 6 6 18',
  alert: 'M12 9v4M12 16.5h.01M10.3 4.2 2.7 17.5A2 2 0 0 0 4.4 20.5h15.2a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5M12 8h.01',
  chevronRight: 'M9.5 6l6 6-6 6',
  chevronDown: 'M6 9.5l6 6 6-6',
  arrowLeft: 'M19 12H5M11 18l-6-6 6-6',
  arrowRight: 'M5 12h14M13 6l6 6-6 6',
  send: 'M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z',
  file: 'M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8zM14 3v5h5',
  shield: 'M12 3 5 6v5.5c0 4.6 3 8.3 7 9.5 4-1.2 7-4.9 7-9.5V6z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5V12l3 2',
  rupee: 'M7 5h10M7 9.5h10M13.5 20 7 13.5h2.5a4.25 4.25 0 0 0 0-8.5',
  sparkle: 'M12 3.5l1.6 4.4 4.4 1.6-4.4 1.6L12 15.5l-1.6-4.4L6 9.5l4.4-1.6zM18.5 15l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z',
  bed: 'M3 18.5V7M3 14h18v4.5M21 14a3 3 0 0 0-3-3h-7v3M7 12.25a1.75 1.75 0 1 0 0-.01',
  activity: 'M3 12h4l2.5-6.5 5 13L17 12h4',
  chat: 'M20 11.5a7.5 7.5 0 0 1-11 6.6L4 19.5l1.4-4.6A7.5 7.5 0 1 1 20 11.5z',
  receipt: 'M6.5 3h11v18l-2.75-1.75L12 21l-2.75-1.75L6.5 21zM9.5 8h5M9.5 12h5M9.5 16h3',
  heart: 'M12 19.5s-7-4.3-8.8-8.6A4.6 4.6 0 0 1 12 6.5a4.6 4.6 0 0 1 8.8 4.4C19 15.2 12 19.5 12 19.5z',
  undo: 'M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11',
  layers: 'M12 3 3 8l9 5 9-5zM3 13l9 5 9-5',
  filter: 'M4 5h16l-6 7.5V19l-4 1.5v-8z',
  stethoscope: 'M6 3v6a4 4 0 0 0 8 0V3M10 13v2a5 5 0 0 0 10 0v-2M20 11.5a1.5 1.5 0 1 0 0-.01',
  logo: 'M8 12.5l3 3 6-7',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0',
  inbox: 'M3 13h5l1.5 2.5h5L16 13h5M5.5 5h13L21 13v5.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5V13z',
}

export function Icon({ name, className = 'h-5 w-5' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.file} />
    </svg>
  )
}

// ---- The billing officer's name, used on every decision. Kept for this browser only. ----------

const OfficerContext = createContext(['Billing Officer', () => {}])
export function OfficerProvider({ children }) {
  const [officer, setOfficer] = useState(() => {
    try {
      return localStorage.getItem('officer') || 'Billing Officer'
    } catch {
      return 'Billing Officer'
    }
  })
  const update = (name) => {
    setOfficer(name)
    try {
      localStorage.setItem('officer', name)
    } catch {
      /* private window: keep it for this session only */
    }
  }
  return <OfficerContext.Provider value={[officer, update]}>{children}</OfficerContext.Provider>
}
export const useOfficer = () => useContext(OfficerContext)

// ---- Toasts ----------------------------------------------------------------------------------

const ToastContext = createContext(() => {})
export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const push = (toast) => {
    const id = `${Date.now()}-${Math.random()}`
    setToasts((shown) => [...shown.slice(-3), { id, ...toast }])
    setTimeout(() => setToasts((shown) => shown.filter((t) => t.id !== id)), toast.duration ?? 5000)
  }
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-50 flex w-[340px] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto flex animate-slide-in items-start gap-3 rounded-xl border border-line bg-white p-3.5 shadow-lift">
            <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full ${t.tone === 'attention' ? 'bg-amber-100 text-amber-700' : t.tone === 'info' ? 'bg-sky-100 text-sky-700' : 'bg-accent-50 text-accent-700'}`}>
              <Icon name={t.icon ?? 'check'} className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{t.title}</p>
              {t.body && <p className="mt-0.5 text-[13px] text-ink-muted">{t.body}</p>}
              {t.action && <button onClick={t.action.onClick} className="mt-1.5 text-[13px] font-medium text-accent-700 hover:underline">{t.action.label}</button>}
            </div>
            <button onClick={() => setToasts((shown) => shown.filter((s) => s.id !== t.id))} className="text-ink-faint hover:text-ink-soft"><Icon name="x" className="h-4 w-4" /></button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
export const useToast = () => useContext(ToastContext)
