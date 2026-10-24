// Dashboard: how the desk is doing, what needs you, and what just happened.
import { Link } from 'react-router-dom'
import { api, useLoad, useTrace } from '../api'
import { nextStep } from '../story'
import { ANSWER_KEYS, BarList, ChartCard, Columns, ModeBars, MODE_COLORS, MUTED, SERIES, SplitBar, StackedBars, Legend } from '../components/charts'
import {
  Avatar, Button, Card, CardHeader, CountUp, EmptyState, Icon, Skeleton, Status, ago, money, pct, procedureName, stagger,
} from '../components/ui'

export default function Dashboard() {
  const numbers = useLoad(api.analytics)
  const claims = useLoad(api.claims)
  useTrace({ onEvent: () => { numbers.refresh(); claims.refresh() } })
  const all = claims.data ?? []
  const tasks = all.filter((c) => c.state === 'AWAITING_HUMAN' || c.state === 'FINAL_APPROVED')
  const recent = [...all].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? '')).slice(0, 6)
  const d = numbers.data
  const today = new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })

  return (
    <>
      <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-ink-muted">{today}</p>
          <h1 className="mt-1 text-[26px] font-semibold tracking-tight">
            {tasks.length ? `${tasks.length} claim${tasks.length > 1 ? 's' : ''} need${tasks.length > 1 ? '' : 's'} you today` : 'You’re all caught up'}
          </h1>
        </div>
        <Link to="/patients/admit"><Button variant="primary" icon="plus">Admit patient</Button></Link>
      </div>

      <div className="mb-8 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4">
        <Metric label="Active claims" value={d?.kpis.active_claims} hint={d ? `${d.kpis.waiting_for_a_person} waiting for a person` : null} />
        <Metric label="Pre-auth approved in full, first answer" value={d?.kpis.approved_in_full_first_time} format={(v) => pct(v / 100)} scale={100}
          hint={d?.kpis.preauth_answers ? `${pct(d.kpis.part_approved_first_time)} more part-approved · ${d.kpis.preauth_answers} answers` : null} />
        <Metric label="First answers without a query" value={d?.kpis.query_free_first_answer} format={(v) => pct(v / 100)} scale={100} hint="All stages, every insurer" />
        <Metric label="Approved by insurers" value={d?.kpis.approved_total} format={money}
          hint={d?.kpis.requested_total ? `${pct(d.kpis.approved_share_of_requested)} of ${money(d.kpis.requested_total)} requested` : null} />
      </div>

      {claims.data && !all.length ? (
        <EmptyState icon="bed" title="No patients yet" action={<Link to="/patients/admit"><Button variant="primary" icon="plus">Admit your first patient</Button></Link>}>
          Admit a patient and the agents prepare their insurance claim while you watch.
        </EmptyState>
      ) : (
        <div className="grid gap-6 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Your tasks" subtitle="Nothing goes to an insurer until you approve it"
              action={tasks.length > 0 && <Link to="/tasks" className="text-[13px] font-medium text-ink-soft hover:text-ink">See all</Link>} />
            {!claims.data ? <ListSkeleton /> : tasks.length ? (
              <ul className="px-2 pb-2">
                {tasks.slice(0, 5).map((c, i) => (
                  <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
                    <Link to={`/patients/${c.claim_id}`} className="group flex items-center gap-3 rounded-lg px-3 py-3 transition hover:bg-zinc-50">
                      <Avatar name={c.patient} size="sm" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{nextStep(c)}</p>
                        <p className="truncate text-[13px] text-ink-muted">{c.patient} · {procedureName(c.procedure)}</p>
                      </div>
                      <span className="text-xs text-ink-faint">{ago(c.updated_at)}</span>
                      <Icon name="arrowRight" className="h-4 w-4 text-ink-faint transition group-hover:translate-x-0.5 group-hover:text-ink" />
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="flex items-center gap-3 px-5 pb-6 text-sm text-ink-muted"><Icon name="check" className="h-4 w-4 text-accent-600" />Nothing waiting. New tasks appear here the moment agents finish.</div>
            )}
          </Card>

          <Card className="lg:col-span-2">
            <CardHeader title="Recently updated" action={<Link to="/patients" className="text-[13px] font-medium text-ink-soft hover:text-ink">All patients</Link>} />
            {!claims.data ? <ListSkeleton /> : (
              <ul className="px-2 pb-2">
                {recent.map((c, i) => (
                  <li key={c.claim_id} className="animate-fade-up" style={stagger(i)}>
                    <Link to={`/patients/${c.claim_id}`} className="flex items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-zinc-50">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{c.patient}</p>
                        <p className="truncate text-xs text-ink-muted">{ago(c.updated_at)}</p>
                      </div>
                      <Status state={c.state} busy={c.busy} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

        </div>
      )}

      {d && d.claims > 0 && <Analytics d={d} />}
      {d?.benchmark && <Benchmark b={d.benchmark} />}
    </>
  )
}

const count = (v) => String(v)

// How the desk is doing, from what the system recorded. Every rupee total comes from the API (rules engine).
function Analytics({ d }) {
  const answerRows = (rows) => rows.map((r) => [r.label, ...ANSWER_KEYS.map((k) => r[k.key])])
  const split = d.deductions_split
  return (
    <section className="mt-10">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] font-semibold tracking-tight">How claims are going</h2>
        <p className="text-xs text-ink-muted">
          Live from {d.claims} claim{d.claims === 1 ? '' : 's'} on this desk
          {d.oversight.scripted_decisions > 0 && ` · includes ${d.oversight.scripted_decisions} scripted demo decisions`}
          {d.kpis.median_package_seconds !== null && ` · pre-auth package ready in ${d.kpis.median_package_seconds}s (median, agent time)`}
        </p>
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <ChartCard title="Where claims are" subtitle="Claims by step, right now"
          table={{ columns: ['Step', 'Claims'], rows: d.pipeline.map((p) => [p.label, p.value]) }}>
          {({ show, hide }) => <BarList data={d.pipeline} format={count} show={show} hide={hide} />}
        </ChartCard>

        <ChartCard title="Insurer's first answer" subtitle="Each stage of each claim counts once"
          table={{ columns: ['Stage', ...ANSWER_KEYS.map((k) => k.label)], rows: answerRows(d.first_answers) }}
          footer="Approved means approved in full; part-approved is counted on its own, never as approved.">
          {({ show, hide }) => d.first_answers.length ? <StackedBars rows={d.first_answers} keys={ANSWER_KEYS} show={show} hide={hide} /> : <Empty>No insurer answers yet</Empty>}
        </ChartCard>

        <ChartCard title="Where money is lost" subtitle="Deductions in insurers' decisions, by the clause they cite"
          table={{ columns: ['Clause', 'Deducted'], rows: d.deductions_by_clause.map((r) => [r.label, money(r.value)]) }}>
          {({ show, hide }) => d.deductions_by_clause.length
            ? <BarList data={d.deductions_by_clause} format={money} show={show} hide={hide} labelWidth="w-32 sm:w-44" />
            : <Empty>No deductions yet</Empty>}
        </ChartCard>

        <ChartCard title="Are the cuts in the policy?" subtitle="The rules engine checks each deduction against the policy wording"
          table={{ columns: ['Part', 'Amount'], rows: [['Backed by the policy wording', money(split.policy_backed)], ['Beyond the wording (judgement cuts)', money(split.disputed)], ['Recovered by appeal', money(split.recovered_by_appeal)]] }}
          footer="A cut beyond the wording isn't automatically wrong: medical-necessity cuts are judged on the records, and are appealed only when the evidence supports it.">
          {({ show, hide }) => (
            <SplitBar show={show} hide={hide} format={money} parts={[
              { label: 'Backed by the policy wording', value: split.policy_backed, color: MUTED },
              { label: 'Beyond the wording (judgement cuts)', value: split.disputed, color: SERIES },
            ]} />
          )}
        </ChartCard>

        <ChartCard title="Human oversight" subtitle="Every message to an insurer passed a person first"
          table={{ columns: ['Decision', 'Count'], rows: [...d.oversight.decisions.map((r) => [r.label, r.value]), ['Records attached', d.oversight.documents_attached], ['Rechecks after a fix', d.oversight.rechecks]] }}
          footer={`${d.oversight.documents_attached} record${d.oversight.documents_attached === 1 ? '' : 's'} attached · ${d.oversight.rechecks} recheck${d.oversight.rechecks === 1 ? '' : 's'} after a fix · ${d.oversight.escalations} escalation${d.oversight.escalations === 1 ? '' : 's'} after an agent failed`}>
          {({ show, hide }) => <BarList data={d.oversight.decisions} format={count} show={show} hide={hide} labelWidth="w-32 sm:w-44" />}
        </ChartCard>

        <ChartCard title="Denial risk at the gate" subtitle="The risk checker's score on each claim's latest package"
          table={{ columns: ['Risk band', 'Claims'], rows: d.risk_histogram.map((r) => [r.label, r.value]) }}
          footer={d.llm.live + d.llm.template ? `Agent text: ${d.llm.live} written by a live model, ${d.llm.template} by labelled templates. Amounts never come from a model.` : null}>
          {({ show, hide }) => <Columns data={d.risk_histogram} show={show} hide={hide} />}
        </ChartCard>

        {d.first_answers_by_tpa.length > 0 && (
          <ChartCard className="lg:col-span-2" title="Pre-auth first answer by insurer" subtitle="The simulated TPAs differ in what they ask for and cut"
            table={{ columns: ['Insurer', ...ANSWER_KEYS.map((k) => k.label)], rows: answerRows(d.first_answers_by_tpa) }}>
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
    <section className="mt-10">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] font-semibold tracking-tight">What each part adds</h2>
        <p className="text-xs text-ink-muted">{b.label} · {b.cases} cases · run {b.generated.slice(0, 10)}</p>
      </div>
      <ChartCard title="Basic submission vs rules checklist vs ClaimPilot" subtitle="The same frozen cases, documents and approval gate in every mode"
        table={{ columns: ['Metric', ...b.modes], rows: b.metrics.map((m) => [m.label, ...b.modes.map((mode) => fmt[m.unit](m.values[mode]))]) }}
        footer={b.notes.join(' ')}>
        {({ show, hide }) => (
          <>
            <Legend keys={keys} />
            <div className="grid gap-6 md:grid-cols-2">
              {b.metrics.map((m) => <ModeBars key={m.key} metric={m} modes={b.modes} show={show} hide={hide} format={fmt[m.unit]} />)}
            </div>
          </>
        )}
      </ChartCard>
    </section>
  )
}

function Empty({ children }) {
  return <p className="py-6 text-center text-sm text-ink-muted">{children}</p>
}

function Metric({ label, value, format = (v) => v, scale = 1, hint }) {
  return (
    <div className="bg-white px-5 py-4">
      <p className="text-[13px] text-ink-muted">{label}</p>
      <div className="mt-1 text-2xl font-semibold tracking-tight tabular">
        {value === undefined ? <Skeleton className="mt-1 h-7 w-20" /> : <CountUp value={value === null ? null : value * scale} format={format} />}
      </div>
      {hint && <p className="mt-0.5 text-xs text-ink-faint">{hint}</p>}
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="space-y-4 px-5 pb-5">
      {[0, 1, 2].map((i) => <div key={i} className="flex items-center gap-3"><Skeleton className="h-8 w-8 rounded-full" /><div className="flex-1 space-y-1.5"><Skeleton className="h-3.5 w-1/3" /><Skeleton className="h-3 w-1/2" /></div></div>)}
    </div>
  )
}
