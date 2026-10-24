// Dashboard: how the desk is doing, what needs you, and where claims are. Every rupee comes from the API.
import { Link, useNavigate } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { useAuth } from '../auth'
import { nextStep } from '../story'
import { ANSWER_KEYS, BarList, ChartCard, Columns, Donut, Legend, ModeBars, MODE_COLORS, MUTED, PillColumns, SERIES, StackedBars } from '../components/charts'
import {
  Avatar, Button, Card, CardHeader, CountUp, EmptyState, Icon, KpiTile, PageHeader, Skeleton, Status, ago, money, pct, procedureName, stagger,
} from '../components/ui'

export default function Dashboard() {
  const numbers = useLoad(api.analytics)
  const claims = useLoad(api.claims)
  const { user } = useAuth()
  useTrace({ onEvent: () => { numbers.refresh(); claims.refresh() } })
  const all = claims.data ?? []
  const tasks = all.filter((c) => (c.state === 'AWAITING_HUMAN' || c.state === 'FINAL_APPROVED') && !c.busy)
  const recent = [...all].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? '')).slice(0, 6)
  const d = numbers.data
  const k = d?.kpis
  const first = user?.display_name ?? ''

  return (
    <>
      <PageHeader eyebrow={new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
        title={<>Welcome back{first ? ', ' : ''}<span className="font-medium text-ink-muted">{first}</span></>}
        subtitle={tasks.length ? `${tasks.length} claim${tasks.length > 1 ? 's' : ''} need${tasks.length > 1 ? '' : 's'} your decision. The agents have done the rest.` : 'Nothing is waiting for you. New tasks appear the moment agents finish.'}
        action={<>
          <Link to="/tasks"><Button icon="tasks">Open tasks</Button></Link>
          <Link to="/patients/admit"><Button variant="primary" icon="plus">Admit patient</Button></Link>
        </>} />

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile icon="users" label="Active claims" value={k ? <CountUp value={k.active_claims} /> : <Skeleton className="mt-1 h-8 w-16" />}
          pill={claims.data ? `${all.length} total` : null} pillTone="neutral" hint={k ? `${k.waiting_for_a_person} waiting for a person` : null} />
        <KpiTile icon="tasks" tone="amber" label="Waiting for you" value={claims.data ? <CountUp value={tasks.length} /> : <Skeleton className="mt-1 h-8 w-16" />}
          pill={tasks.length ? 'Action needed' : 'All clear'} pillTone={tasks.length ? 'attention' : 'good'} hint="Approve, edit or hold back" />
        <KpiTile icon="check" tone="blue" label="Pre-auth approved in full, first answer"
          value={k ? <CountUp value={k.approved_in_full_first_time === null ? null : Math.round(k.approved_in_full_first_time * 100)} format={(v) => `${v}%`} /> : <Skeleton className="mt-1 h-8 w-16" />}
          pill={k?.preauth_answers ? `${k.preauth_answers} answers` : null} pillTone="info" hint={k?.preauth_answers ? `${pct(k.part_approved_first_time)} more part-approved` : 'No insurer answers yet'} />
        <KpiTile icon="rupee" tone="violet" label="Approved by insurers" value={k ? <CountUp value={k.approved_total} format={money} /> : <Skeleton className="mt-1 h-8 w-24" />}
          pill={k?.requested_total ? `${pct(k.approved_share_of_requested)} of asked` : null} hint={k?.requested_total ? `of ${money(k.requested_total)} requested` : null} />
      </div>

      {claims.data && !all.length ? (
        <EmptyState icon="bed" title="No patients yet" action={<Link to="/patients/admit"><Button variant="primary" icon="plus">Admit your first patient</Button></Link>}>
          Admit a patient and the agents prepare their insurance claim while you watch.
        </EmptyState>
      ) : (
        <>
          <div className="mb-6 grid gap-6 xl:grid-cols-12">
            <Hero k={k} split={d?.deductions_split} tasks={tasks.length} className="xl:col-span-4" />
            <ChartCard className="xl:col-span-5" icon="chart" title="Where claims are" subtitle="Claims at each step, right now"
              table={d && { columns: ['Step', 'Claims'], rows: d.pipeline.map((p) => [p.label, p.value]) }}>
              {({ show, hide }) => d ? <PillColumns data={d.pipeline.map((p) => ({ ...p, label: shortStep(p.label) }))} show={show} hide={hide} /> : <Skeleton className="h-48" />}
            </ChartCard>
            <TaskRail tasks={tasks} loading={!claims.data} className="xl:col-span-3" />
          </div>

          <div className="mb-8 grid gap-6 xl:grid-cols-12">
            <Card className="xl:col-span-7">
              <CardHeader icon="users" title="Recently updated" subtitle="Patients whose claims moved most recently"
                action={<Link to="/patients" className="rounded-full px-3 py-1.5 text-[13px] font-semibold text-accent-700 transition hover:bg-accent-50">View all</Link>} />
              <RecentTable rows={recent} loading={!claims.data} />
            </Card>
            <ChartCard className="xl:col-span-5" icon="building" title="Insurer's first answer" subtitle="Each stage of each claim counts once"
              table={d && { columns: ['Stage', ...ANSWER_KEYS.map((x) => x.label)], rows: d.first_answers.map((r) => [r.label, ...ANSWER_KEYS.map((x) => r[x.key])]) }}
              footer="Approved means approved in full; part-approved is counted on its own.">
              {({ show, hide }) => d?.first_answers.length ? <StackedBars rows={d.first_answers} keys={ANSWER_KEYS} show={show} hide={hide} /> : <Empty>No insurer answers yet</Empty>}
            </ChartCard>
          </div>
        </>
      )}

      {d && d.claims > 0 && <Analytics d={d} />}
      {d?.benchmark && <Benchmark b={d.benchmark} />}
    </>
  )
}

const shortStep = (text) => text.replace(/^Waiting for /, '').replace(/^With the /, '').replace(/^\w/, (c) => c.toUpperCase())

// The green hero: what insurers have approved, against what was asked.
function Hero({ k, split, tasks, className = '' }) {
  const navigate = useNavigate()
  const share = k?.approved_share_of_requested ?? 0
  return (
    <section className={`hero relative flex flex-col overflow-hidden rounded-xl p-6 text-white shadow-lift ${className}`}>
      <span className="absolute -bottom-16 -right-10 h-48 w-48 rounded-full border-[28px] border-white/5" />
      <span className="absolute -right-6 top-10 h-24 w-24 rounded-full border-[18px] border-white/5" />
      <div className="relative flex items-center justify-between">
        <p className="text-[13px] font-semibold text-white/75">Approved by insurers</p>
        <span className="grid h-9 w-9 place-items-center rounded-full bg-white/15"><Icon name="trending" className="h-[18px] w-[18px]" /></span>
      </div>
      <p className="relative mt-3 text-[34px] font-extrabold leading-none tracking-tight tabular">{k ? <CountUp value={k.approved_total} format={money} /> : '—'}</p>
      <p className="relative mt-2 text-[13px] text-white/70">of {money(k?.requested_total)} requested</p>
      <div className="relative mt-4 h-2.5 overflow-hidden rounded-full bg-white/15">
        <div className="stripes h-full origin-left animate-grow-x rounded-full bg-white" style={{ width: `${Math.min(100, share * 100)}%` }} />
      </div>
      <div className="relative mt-5 grid grid-cols-2 gap-3">
        <div className="rounded-2xl bg-white/10 p-3">
          <p className="text-[11px] font-medium text-white/65">Recovered by appeal</p>
          <p className="mt-0.5 text-base font-bold tabular">{money(split?.recovered_by_appeal)}</p>
        </div>
        <div className="rounded-2xl bg-white/10 p-3">
          <p className="text-[11px] font-medium text-white/65">Cuts beyond policy</p>
          <p className="mt-0.5 text-base font-bold tabular">{money(split?.disputed)}</p>
        </div>
      </div>
      <div className="relative mt-auto flex gap-2 pt-5">
        <button onClick={() => navigate('/tasks')} className="inline-flex flex-1 items-center justify-center gap-2 rounded-full bg-white px-4 py-2.5 text-sm font-bold text-accent-700 transition hover:bg-accent-50 active:scale-[0.98]">
          {tasks ? `Review ${tasks} task${tasks === 1 ? '' : 's'}` : 'Open tasks'} <Icon name="arrowRight" className="h-4 w-4" />
        </button>
        <button onClick={() => navigate('/insurer')} className="inline-flex items-center justify-center gap-2 rounded-full border border-white/30 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-white/10">
          Insurer
        </button>
      </div>
    </section>
  )
}

// What needs the officer, as a green timeline.
function TaskRail({ tasks, loading, className = '' }) {
  return (
    <Card className={`flex flex-col ${className}`}>
      <CardHeader title="Your queue" subtitle="Decisions only you can make"
        action={tasks.length > 0 && <span className="rounded-full bg-accent-600 px-2.5 py-0.5 text-xs font-bold text-white tabular">{tasks.length}</span>} />
      <div className="min-h-0 flex-1 px-5 pb-5">
        {loading ? <Skeleton className="h-40 rounded-2xl" /> : tasks.length ? (
          <ol className="relative space-y-3 pl-5">
            <span className="absolute bottom-3 left-[5px] top-3 w-px bg-accent-200" />
            {tasks.slice(0, 4).map((c, i) => (
              <li key={c.claim_id} className="relative animate-fade-up" style={stagger(i)}>
                <span className="absolute -left-5 top-4 h-[11px] w-[11px] rounded-full border-2 border-white bg-accent-500 ring-2 ring-accent-200" />
                <Link to={`/patients/${c.claim_id}`} className="group block rounded-2xl bg-accent-600 p-3.5 text-white shadow-glow transition hover:-translate-y-0.5 hover:bg-accent-700">
                  <div className="flex items-center justify-between gap-2">
                    <p className="truncate text-sm font-bold">{nextStep(c)}</p>
                    <span className="shrink-0 text-[11px] text-white/70">{ago(c.updated_at)}</span>
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <Avatar name={c.patient} size="xs" className="ring-2 ring-white/30" />
                    <p className="min-w-0 flex-1 truncate text-xs text-white/80">{c.patient} · {procedureName(c.procedure)}</p>
                    <span className="grid h-6 w-6 place-items-center rounded-full bg-white/15 transition group-hover:bg-white group-hover:text-accent-700"><Icon name="arrowRight" className="h-3.5 w-3.5" /></span>
                  </div>
                </Link>
              </li>
            ))}
          </ol>
        ) : (
          <div className="grid h-full min-h-[160px] place-items-center rounded-2xl bg-accent-50/60 p-4 text-center">
            <div>
              <span className="mx-auto grid h-10 w-10 place-items-center rounded-full bg-white text-accent-600 shadow-card"><Icon name="check" className="h-5 w-5" /></span>
              <p className="mt-2 text-sm font-bold text-accent-800">All caught up</p>
              <p className="text-xs text-accent-700/80">Nothing needs a decision.</p>
            </div>
          </div>
        )}
        {tasks.length > 4 && <Link to="/tasks" className="mt-3 block text-center text-[13px] font-semibold text-accent-700 hover:underline">+{tasks.length - 4} more</Link>}
      </div>
    </Card>
  )
}

function RecentTable({ rows, loading }) {
  if (loading) return <div className="space-y-3 px-6 pb-6">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-11 rounded-2xl" />)}</div>
  return (
    <div className="px-3 pb-3">
      <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1.2fr)_110px_120px] gap-3 px-3 pb-2 text-[11px] font-bold uppercase tracking-[0.08em] text-ink-faint">
        <span>Patient</span><span>Next step</span><span className="text-right">Approved</span><span className="text-right">Status</span>
      </div>
      <ul className="space-y-1">
        {rows.map((c, i) => (
          <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
            <Link to={`/patients/${c.claim_id}`} className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1.2fr)_110px_120px] items-center gap-3 rounded-2xl px-3 py-2.5 transition hover:bg-canvas">
              <span className="flex min-w-0 items-center gap-3">
                <Avatar name={c.patient} size="sm" />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-bold">{c.patient}</span>
                  <span className="block truncate text-xs text-ink-muted">{procedureName(c.procedure)}</span>
                </span>
              </span>
              <span className="min-w-0">
                <span className="block truncate text-[13px] font-medium text-ink-soft">{nextStep(c)}</span>
                <span className="block text-[11px] text-ink-faint">{ago(c.updated_at)}</span>
              </span>
              <span className="text-right text-sm font-bold tabular">{money(c.approved)}</span>
              <span className="flex justify-end"><Status state={c.state} busy={c.busy} /></span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}

const count = (v) => String(v)

// How the desk is doing, from what the system recorded. Every rupee total comes from the API (rules engine).
function Analytics({ d }) {
  const split = d.deductions_split
  return (
    <section>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-[13px] font-semibold text-accent-600">Analytics</p>
          <h2 className="text-xl font-bold tracking-tight">How claims are going</h2>
        </div>
        <p className="max-w-xl text-right text-xs text-ink-muted">
          Live from {d.claims} claim{d.claims === 1 ? '' : 's'} on this desk
          {d.oversight.scripted_decisions > 0 && ` · includes ${d.oversight.scripted_decisions} scripted demo decisions`}
          {d.kpis.median_package_seconds !== null && ` · pre-auth ready in ${d.kpis.median_package_seconds}s (median agent time)`}
        </p>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard icon="rupee" title="Where money is lost" subtitle="Deductions in insurers' decisions, by the clause they cite"
          table={{ columns: ['Clause', 'Deducted'], rows: d.deductions_by_clause.map((r) => [r.label, money(r.value)]) }}>
          {({ show, hide }) => d.deductions_by_clause.length
            ? <BarList data={d.deductions_by_clause} format={money} show={show} hide={hide} labelWidth="w-32 sm:w-44" />
            : <Empty>No deductions yet</Empty>}
        </ChartCard>

        <ChartCard icon="shield" title="Are the cuts in the policy?" subtitle="The rules engine checks each deduction against the policy wording"
          table={{ columns: ['Part', 'Amount'], rows: [['Backed by the policy wording', money(split.policy_backed)], ['Beyond the wording (judgement cuts)', money(split.disputed)], ['Recovered by appeal', money(split.recovered_by_appeal)]] }}
          footer="A cut beyond the wording isn't automatically wrong: medical-necessity cuts are judged on the records, and appealed only when the evidence supports it.">
          {({ show, hide }) => (
            <Donut show={show} hide={hide} format={money} center={money(split.recovered_by_appeal)} caption="recovered" parts={[
              { label: 'Backed by the policy wording', value: split.policy_backed, color: MUTED },
              { label: 'Beyond the wording', value: split.disputed, color: '#efb23f' },
              { label: 'Recovered by appeal', value: split.recovered_by_appeal, color: SERIES },
            ]} />
          )}
        </ChartCard>

        <ChartCard icon="user" title="Human oversight" subtitle="Every message to an insurer passed a person first"
          table={{ columns: ['Decision', 'Count'], rows: [...d.oversight.decisions.map((r) => [r.label, r.value]), ['Records attached', d.oversight.documents_attached], ['Rechecks after a fix', d.oversight.rechecks]] }}
          footer={`${d.oversight.documents_attached} record${d.oversight.documents_attached === 1 ? '' : 's'} attached · ${d.oversight.rechecks} recheck${d.oversight.rechecks === 1 ? '' : 's'} after a fix · ${d.oversight.escalations} escalation${d.oversight.escalations === 1 ? '' : 's'} after an agent failed`}>
          {({ show, hide }) => <BarList data={d.oversight.decisions} format={count} show={show} hide={hide} labelWidth="w-32 sm:w-44" />}
        </ChartCard>

        <ChartCard icon="activity" title="Denial risk at the gate" subtitle="The risk checker's score on each claim's latest package"
          table={{ columns: ['Risk band', 'Claims'], rows: d.risk_histogram.map((r) => [r.label, r.value]) }}
          footer={d.llm.live + d.llm.template ? `Agent text: ${d.llm.live} written by a live model, ${d.llm.template} by labelled templates. Amounts never come from a model.` : null}>
          {({ show, hide }) => <Columns data={d.risk_histogram} show={show} hide={hide} />}
        </ChartCard>

        {d.first_answers_by_tpa.length > 0 && (
          <ChartCard className="lg:col-span-2" icon="building" title="Pre-auth first answer by insurer" subtitle="The simulated TPAs differ in what they ask for and cut"
            table={{ columns: ['Insurer', ...ANSWER_KEYS.map((x) => x.label)], rows: d.first_answers_by_tpa.map((r) => [r.label, ...ANSWER_KEYS.map((x) => r[x.key])]) }}>
            {({ show, hide }) => <StackedBars rows={d.first_answers_by_tpa} keys={ANSWER_KEYS} show={show} hide={hide} />}
          </ChartCard>
        )}
      </div>
    </section>
  )
}

// The offline benchmark, kept apart from the live numbers and labelled as synthetic.
function Benchmark({ b }) {
  const fmt = { count, share: (v) => pct(v), rupees: money }
  const keys = b.modes.map((m) => ({ key: m, label: m, color: MODE_COLORS[m] }))
  return (
    <section className="mt-8">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-[13px] font-semibold text-accent-600">Benchmark · synthetic</p>
          <h2 className="text-xl font-bold tracking-tight">What each part adds</h2>
        </div>
        <p className="text-xs text-ink-muted">{b.label} · {b.cases} cases · run {b.generated.slice(0, 10)}</p>
      </div>
      <ChartCard icon="chart" title="Basic submission vs rules checklist vs ClaimPilot" subtitle="The same frozen cases, documents and approval gate in every mode"
        table={{ columns: ['Metric', ...b.modes], rows: b.metrics.map((m) => [m.label, ...b.modes.map((mode) => fmt[m.unit](m.values[mode]))]) }}
        footer={b.notes.join(' ')}>
        {({ show, hide }) => (
          <>
            <Legend keys={keys} />
            <div className="grid gap-4 md:grid-cols-2">
              {b.metrics.map((m) => <ModeBars key={m.key} metric={m} modes={b.modes} show={show} hide={hide} format={fmt[m.unit]} />)}
            </div>
          </>
        )}
      </ChartCard>
    </section>
  )
}

function Empty({ children }) {
  return <p className="py-8 text-center text-sm text-ink-muted">{children}</p>
}
