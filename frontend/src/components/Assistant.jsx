// The ClaimPilot assistant: a floating chat for patients (their cover and reviewed updates) and
// officers (a read-only copilot on a claim or the desk). Every answer shows its numbered sources and
// whether it passed the verifier; a question that needs a person goes to the billing officer.
import { useEffect, useRef, useState } from 'react'
import { request } from '../api'
import { Icon, Spinner, stagger } from './ui'

const PROMPTS = {
  patient: ['Is there a limit on room rent?', 'How much will I have to pay?', 'What is a co-pay?', 'What is the latest update on my claim?'],
  officer: ['What should I fix before I send this?', 'Why did the insurer cut this claim?', 'What does the policy say about room rent?', 'Which claims are waiting for me?'],
  officerDesk: ['Which claims are waiting for me?', 'How many claims are on the desk?', 'What is proportionate deduction?'],
}

export default function Assistant({ mode, claimId, patientName }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const end = useRef(null)
  const base = mode === 'patient' ? '/api/portal/assistant' : '/api/assistant'

  // Other parts of the app can open the assistant (the sidebar's "Ask ClaimPilot").
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('open-assistant', show)
    return () => window.removeEventListener('open-assistant', show)
  }, [])

  // A patient's earlier questions, with any officer replies, are loaded when the chat opens.
  useEffect(() => {
    if (!open || mode !== 'patient') return undefined
    const load = () => request('GET', `${base}/history${claimId ? `?claim_id=${claimId}` : ''}`)
      .then((data) => setMessages(data.history.flatMap((h) => [{ role: 'you', text: h.question }, { role: 'bot', ...h }])))
      .catch(() => {})
    load()
    const timer = setInterval(load, 8000) // officer replies arrive while the chat is open
    return () => clearInterval(timer)
  }, [open, mode, claimId, base])

  // On wide screens the page makes room for the open chat instead of hiding under it (see index.css).
  useEffect(() => {
    document.body.dataset.assistant = open ? 'open' : 'closed'
    return () => { delete document.body.dataset.assistant }
  }, [open])

  useEffect(() => { setMessages([]) }, [claimId, mode])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [messages, busy, open])

  const ask = async (question) => {
    const text = question.trim()
    if (!text || busy) return
    setDraft('')
    setError('')
    setMessages((shown) => [...shown, { role: 'you', text }])
    setBusy(true)
    try {
      const answer = await request('POST', `${base}/ask`, { question: text, claim_id: claimId ?? null })
      setMessages((shown) => [...shown, { role: 'bot', ...answer }])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const prompts = mode === 'patient' ? PROMPTS.patient : claimId ? PROMPTS.officer : PROMPTS.officerDesk
  const title = mode === 'patient' ? 'Ask about your cover' : 'ClaimPilot copilot'
  const subtitle = mode === 'patient' ? 'From your policy and reviewed updates' : claimId ? `On ${patientName ?? claimId}` : 'On the whole desk'

  return (
    <>
      {!open && (
        <button onClick={() => setOpen(true)} aria-label={title}
          className="group fixed bottom-6 right-6 z-40 flex animate-scale-in items-center gap-2.5 rounded-full bg-accent-600 py-3 pl-3.5 pr-5 text-sm font-bold text-white shadow-glow transition hover:-translate-y-0.5 hover:bg-accent-700">
          <span className="relative grid h-8 w-8 place-items-center rounded-full bg-white/15">
            <Icon name="bot" className="h-[18px] w-[18px]" />
            <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-ring rounded-full bg-white" />
          </span>
          {mode === 'patient' ? 'Ask a question' : 'Ask ClaimPilot'}
        </button>
      )}
      {open && (
        <section className="fixed bottom-6 right-6 z-40 flex h-[min(640px,calc(100vh-48px))] w-[min(420px,calc(100vw-32px))] animate-slide-up flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-pop" aria-label={title}>
          <header className="hero relative flex items-center gap-3 px-5 py-4 text-white">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-2xl bg-white/15"><Icon name="bot" className="h-5 w-5" /></span>
            <div className="min-w-0 flex-1">
              <p className="font-bold">{title}</p>
              <p className="truncate text-xs text-white/75">{subtitle}</p>
            </div>
            <button onClick={() => setOpen(false)} className="grid h-8 w-8 place-items-center rounded-full text-white/80 transition hover:bg-white/15 hover:text-white" aria-label="Close"><Icon name="x" className="h-4 w-4" /></button>
          </header>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto bg-canvas/60 px-4 py-5">
            <Welcome mode={mode} />
            {messages.map((m, i) => (m.role === 'you' ? <You key={i} text={m.text} /> : <Bot key={i} answer={m} index={i} />))}
            {busy && (
              <div className="flex animate-fade-in items-center gap-2 pl-10">
                <span className="flex gap-1 rounded-2xl rounded-tl-md bg-white px-4 py-3 shadow-card">
                  {[0, 1, 2].map((d) => <span key={d} className="h-2 w-2 animate-dots rounded-full bg-accent-500" style={{ animationDelay: `${d * 150}ms` }} />)}
                </span>
                <span className="text-xs text-ink-muted">Checking the sources…</span>
              </div>
            )}
            {error && <p className="rounded-2xl bg-rose-50 px-3 py-2 text-sm text-rose-800">{error}</p>}
            <div ref={end} />
          </div>

          <div className="border-t border-line bg-white p-3">
            <div className="mb-2.5 flex gap-1.5 overflow-x-auto pb-1">
              {prompts.map((p) => (
                <button key={p} onClick={() => ask(p)} disabled={busy}
                  className="shrink-0 rounded-full border border-accent-200 bg-accent-50/60 px-3 py-1.5 text-xs font-semibold text-accent-700 transition hover:bg-accent-100 disabled:opacity-50">{p}</button>
              ))}
            </div>
            <form onSubmit={(e) => { e.preventDefault(); ask(draft) }} className="flex items-center gap-2">
              <input value={draft} onChange={(e) => setDraft(e.target.value)} maxLength={500} placeholder={mode === 'patient' ? 'Ask about your cover or claim…' : 'Ask about this claim, policy or the desk…'}
                className="h-11 min-w-0 flex-1 rounded-full border border-line bg-canvas px-4 text-sm outline-none transition placeholder:text-ink-faint focus:border-accent-300 focus:bg-white focus:ring-4 focus:ring-accent-100/70" />
              <button type="submit" disabled={busy || draft.trim().length < 2} aria-label="Send"
                className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-accent-600 text-white shadow-glow transition hover:bg-accent-700 active:scale-95 disabled:bg-accent-200 disabled:shadow-none">
                {busy ? <Spinner className="h-4 w-4 border-white/30 border-t-white" /> : <Icon name="send" className="h-4 w-4" />}
              </button>
            </form>
          </div>
        </section>
      )}
    </>
  )
}

function Welcome({ mode }) {
  return (
    <div className="flex gap-2.5">
      <BotAvatar />
      <div className="max-w-[85%] rounded-2xl rounded-tl-md bg-white px-4 py-3 text-sm leading-relaxed text-ink-soft shadow-card">
        {mode === 'patient'
          ? <>Hi! I answer from <b>your policy</b> and the <b>updates your billing officer has reviewed</b>, and I show my sources. Questions that need a decision go straight to your billing officer.</>
          : <>Ask about the open claim, its policy, or the desk. I only use the claim&apos;s record and policy, cite each source, and can&apos;t send or approve anything.</>}
      </div>
    </div>
  )
}

const BotAvatar = () => <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-600 text-white shadow-glow"><Icon name="bot" className="h-4 w-4" /></span>

function You({ text }) {
  return (
    <div className="flex animate-fade-up justify-end">
      <p className="max-w-[80%] rounded-2xl rounded-tr-md bg-accent-600 px-4 py-2.5 text-sm text-white shadow-glow">{text}</p>
    </div>
  )
}

const KIND = { policy: 'Your policy', claim: 'Reviewed update', glossary: 'Glossary', reference: 'Reference wording', thread: 'Insurer', agent: 'Agent', desk: 'Desk' }

function Bot({ answer, index }) {
  const [shown, setShown] = useState(null)
  const parts = String(answer.answer ?? '').split(/(\[S\d+\])/g)
  const handoff = answer.handoff
  return (
    <div className="flex animate-fade-up gap-2.5" style={stagger(index % 3, 40)}>
      <BotAvatar />
      <div className="min-w-0 max-w-[88%] space-y-2">
        <div className="whitespace-pre-line rounded-2xl rounded-tl-md bg-white px-4 py-3 text-sm leading-relaxed text-ink shadow-card">
          {parts.map((part, i) => {
            const cite = part.match(/^\[(S\d+)\]$/)
            if (!cite) return <span key={i}>{part}</span>
            return (
              <button key={i} onClick={() => setShown(shown === cite[1] ? null : cite[1])}
                className={`mx-0.5 inline-grid h-5 min-w-5 place-items-center rounded-full px-1 align-middle text-[10px] font-bold transition ${shown === cite[1] ? 'bg-accent-600 text-white' : 'bg-accent-50 text-accent-700 hover:bg-accent-100'}`}>
                {cite[1].slice(1)}
              </button>
            )
          })}
          {answer.sources?.length > 0 && (
            <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
              {answer.verified
                ? <span className="inline-flex items-center gap-1 rounded-full bg-accent-50 px-2 py-0.5 text-[11px] font-bold text-accent-700"><Icon name="shieldCheck" className="h-3.5 w-3.5" />Checked against sources</span>
                : <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-bold text-amber-700"><Icon name="alert" className="h-3.5 w-3.5" />Not verified</span>}
              <span className="text-[11px] text-ink-faint">{answer.method === 'llm' ? 'Phrased by AI from the sources' : 'Quoted from the sources'} · {answer.sources.length} source{answer.sources.length === 1 ? '' : 's'}</span>
            </div>
          )}
        </div>
        {shown && answer.sources?.filter((s) => s.id === shown).map((s) => (
          <div key={s.id} className="animate-fade-in rounded-2xl border border-accent-100 bg-accent-50/50 px-3.5 py-2.5 text-xs">
            <p className="font-bold text-accent-800">[{s.id}] {KIND[s.kind] ?? s.kind}</p>
            <p className="mt-0.5 font-semibold text-ink-soft">{s.label}</p>
            <p className="mt-1 leading-relaxed text-ink-muted">{s.text}</p>
          </div>
        ))}
        {handoff && (
          <div className={`rounded-2xl border px-3.5 py-3 text-xs ${handoff.status === 'answered' ? 'border-accent-200 bg-white' : 'border-amber-200 bg-amber-50/60'}`}>
            <p className="flex items-center gap-1.5 font-bold">
              <Icon name={handoff.status === 'answered' ? 'user' : 'clock'} className={`h-3.5 w-3.5 ${handoff.status === 'answered' ? 'text-accent-600' : 'text-amber-600'}`} />
              {handoff.status === 'answered' ? `Reply from ${handoff.reply.officer}` : 'Sent to your billing officer · waiting for a reply'}
            </p>
            {handoff.status === 'answered' && <p className="mt-1.5 text-sm leading-relaxed text-ink">{handoff.reply.answer}</p>}
          </div>
        )}
      </div>
    </div>
  )
}
