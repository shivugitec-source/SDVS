import { useState } from 'react';

type Language = 'en' | 'kn';
type Script = { script_id: string; anonymous_code: string; subject_name: string; page_count: number; status: string };
type Question = { id: string; question_number: string; max_marks: number; rubric?: string | null };

const copy = {
  en: {
    title: 'Shivashirwad Digital Valuation System', sub: 'Secure • Anonymous • Question-wise evaluation',
    dashboard: 'Examination dashboard', assigned: 'Assigned scripts', pending: 'Pending',
    open: 'Open evaluation', q: 'Question', max: 'Maximum marks', marks: 'Marks awarded',
    remark: 'Evaluation remark', private: 'Private evaluator note', save: 'Save draft',
    submit: 'Review & submit', warning: 'Candidate identity is hidden. Submitted valuations cannot be edited.',
    lang: 'ಕನ್ನಡ', email: 'Registered email', send: 'Send verification codes',
    emailOtp: 'Email verification code', phoneOtp: 'Mobile verification code',
    verifyEmail: 'Verify email', verifyPhone: 'Verify mobile', otpHint: 'Enter the six-digit code sent to your registered contact.',
    signOut: 'Sign out', noScripts: 'No scripts assigned to this account.', secure: 'Secure session',
    scriptsCount: 'Assigned scripts', completed: 'Submitted', pdfError: 'Unable to load the answer-script PDF.',
    allQuestions: 'Every question must be marked before submission.'
  },
  kn: {
    title: 'ಶಿವಾಶೀರ್ವಾದ ಡಿಜಿಟಲ್ ಮೌಲ್ಯಮಾಪನ ವ್ಯವಸ್ಥೆ', sub: 'ಸುರಕ್ಷಿತ • ಅನಾಮಧೇಯ • ಪ್ರಶ್ನಾವಾರು ಮೌಲ್ಯಮಾಪನ',
    dashboard: 'ಪರೀಕ್ಷಾ ಡ್ಯಾಶ್‌ಬೋರ್ಡ್', assigned: 'ನಿಯೋಜಿತ ಉತ್ತರ ಪತ್ರಿಕೆಗಳು', pending: 'ಬಾಕಿ',
    open: 'ಮೌಲ್ಯಮಾಪನ ತೆರೆಯಿರಿ', q: 'ಪ್ರಶ್ನೆ', max: 'ಗರಿಷ್ಠ ಅಂಕಗಳು', marks: 'ನೀಡಿದ ಅಂಕಗಳು',
    remark: 'ಮೌಲ್ಯಮಾಪನ ಟಿಪ್ಪಣಿ', private: 'ಖಾಸಗಿ ಟಿಪ್ಪಣಿ', save: 'ಕರಡು ಉಳಿಸಿ',
    submit: 'ಪರಿಶೀಲಿಸಿ ಮತ್ತು ಸಲ್ಲಿಸಿ', warning: 'ಅಭ್ಯರ್ಥಿಯ ಗುರುತು ಮರೆಮಾಡಲಾಗಿದೆ.', lang: 'English',
    email: 'ನೋಂದಾಯಿತ ಇಮೇಲ್', send: 'ಪರಿಶೀಲನಾ ಕೋಡ್ ಕಳುಹಿಸಿ', emailOtp: 'ಇಮೇಲ್ ಪರಿಶೀಲನಾ ಕೋಡ್',
    phoneOtp: 'ಮೊಬೈಲ್ ಪರಿಶೀಲನಾ ಕೋಡ್', verifyEmail: 'ಇಮೇಲ್ ಪರಿಶೀಲಿಸಿ', verifyPhone: 'ಮೊಬೈಲ್ ಪರಿಶೀಲಿಸಿ',
    otpHint: 'ನೋಂದಾಯಿತ ಸಂಪರ್ಕಕ್ಕೆ ಬಂದ ಆರು ಅಂಕಿಯ ಕೋಡ್ ನಮೂದಿಸಿ.', signOut: 'ನಿರ್ಗಮಿಸಿ',
    noScripts: 'ಈ ಖಾತೆಗೆ ಯಾವುದೇ ಪತ್ರಿಕೆಗಳನ್ನು ನಿಯೋಜಿಸಲಾಗಿಲ್ಲ.', secure: 'ಸುರಕ್ಷಿತ ಸೆಷನ್',
    scriptsCount: 'ನಿಯೋಜಿತ ಪತ್ರಿಕೆಗಳು', completed: 'ಸಲ್ಲಿಸಲಾಗಿದೆ', pdfError: 'ಉತ್ತರ ಪತ್ರಿಕೆಯ PDF ತೆರೆಯಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ.',
    allQuestions: 'ಸಲ್ಲಿಸುವ ಮೊದಲು ಪ್ರತಿಯೊಂದು ಪ್ರಶ್ನೆಗೆ ಅಂಕಗಳನ್ನು ನಮೂದಿಸಿ.'
  }
};

export default function App() {
  const [lang, setLang] = useState<Language>('en');
  const [token, setToken] = useState('');
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [otpStage, setOtpStage] = useState<'email' | 'phone' | 'authenticated'>('email');
  const [otpRequested, setOtpRequested] = useState(false);
  const [msg, setMsg] = useState('');
  const [scripts, setScripts] = useState<Script[]>([]);
  const [active, setActive] = useState<Script | null>(null);
  const [questions, setQuestions] = useState<Question[]>([]);
  const [marks, setMarks] = useState<Record<string, string | number>>({});
  const [remarks, setRemarks] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [pdf, setPdf] = useState('');
  const [busy, setBusy] = useState(false);
  const L = copy[lang];
  const api = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

  async function request(path: string, options: RequestInit = {}, auth = token) {
    const response = await fetch(api + path, {
      ...options,
      headers: {
        ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...(auth ? { Authorization: 'Bearer ' + auth } : {}),
        ...options.headers
      }
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      const detail = body.detail;
      throw new Error(typeof detail === 'string' ? detail : detail?.message || response.statusText || 'Request failed');
    }
    return response;
  }

  async function load(auth = token) {
    try {
      const response = await request('/evaluator/scripts', {}, auth);
      setScripts(await response.json());
      setMsg('');
    } catch (error) {
      setMsg(error instanceof Error ? error.message : 'Unable to load assigned scripts.');
    }
  }

  async function sendCodes(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    try {
      const response = await request('/auth/request-otp', {
        method: 'POST', body: JSON.stringify({ email: email.trim().toLowerCase() })
      }, '');
      const result = await response.json();
      setOtpRequested(true);
      setOtpStage('email');
      setOtp('');
      setMsg(result.message || 'Verification codes sent to your registered email and mobile.');
    } catch (error) {
      setMsg(error instanceof Error ? error.message : 'Unable to send verification codes.');
    } finally {
      setBusy(false);
    }
  }

  async function verifyCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!/^\d{6}$/.test(otp)) {
      setMsg('Enter the six-digit verification code.');
      return;
    }
    setBusy(true);
    try {
      const channel = otpStage === 'email' ? 'email' : 'phone';
      const response = await request('/auth/verify-otp', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim().toLowerCase(), channel, code: otp })
      }, '');
      const result = await response.json();
      setOtp('');
      if (result.access_token && result.both_channels_verified) {
        setToken(result.access_token);
        setOtpStage('authenticated');
        setOtpRequested(false);
        await load(result.access_token);
        setMsg('Both verification channels confirmed. Secure evaluator session started.');
      } else {
        setOtpStage(result.next_channel === 'email' ? 'email' : 'phone');
        setMsg(channel === 'email' ? 'Email verified. Now verify your registered mobile number.' : 'Mobile verified. Verify your email to finish sign-in.');
      }
    } catch (error) {
      setMsg(error instanceof Error ? error.message : 'OTP verification failed.');
    } finally {
      setBusy(false);
    }
  }

  async function openScript(script: Script) {
    setActive(script);
    if (pdf) URL.revokeObjectURL(pdf);
    setPdf('');
    try {
      const [questionResponse, evaluationResponse, pdfResponse] = await Promise.all([
        request('/evaluator/scripts/' + script.script_id + '/questions'),
        request('/evaluator/scripts/' + script.script_id + '/evaluation'),
        request('/evaluator/scripts/' + script.script_id + '/pdf')
      ]);
      const questionData: Question[] = await questionResponse.json();
      const evaluation = await evaluationResponse.json();
      setQuestions(questionData);
      setMarks(Object.fromEntries(evaluation.marks.map((mark: any) => [mark.question_id, mark.marks])));
      setRemarks(Object.fromEntries(evaluation.marks.map((mark: any) => [mark.question_id, mark.remark || ''])));
      setNotes(Object.fromEntries(evaluation.marks.map((mark: any) => [mark.question_id, mark.private_note || ''])));
      if (!pdfResponse.headers.get('content-type')?.includes('application/pdf')) throw new Error(L.pdfError);
      setPdf(URL.createObjectURL(await pdfResponse.blob()));
      setMsg('');
    } catch (error) {
      setMsg(error instanceof Error ? error.message : L.pdfError);
      setActive(null);
    }
  }

  async function saveDraft() {
    if (!active) return null;
    setBusy(true);
    try {
      const response = await request('/evaluations', {
        method: 'POST',
        body: JSON.stringify({
          script_id: active.script_id,
          marks: questions.map(question => ({
            question_id: question.id,
            marks: Number(marks[question.id] ?? 0),
            remark: remarks[question.id] || '',
            private_note: notes[question.id] || ''
          }))
        })
      });
      return await response.json();
    } catch (error) {
      setMsg(error instanceof Error ? error.message : 'Unable to save evaluation.');
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function submitEvaluation() {
    if (questions.some(question => marks[question.id] === undefined || marks[question.id] === '')) {
      setMsg(L.allQuestions);
      return;
    }
    const saved = await saveDraft();
    if (!saved) return;
    setBusy(true);
    try {
      await request('/evaluations/' + saved.id + '/submit', { method: 'POST' });
      setMsg('Valuation submitted and locked.');
      setActive(null);
      if (pdf) URL.revokeObjectURL(pdf);
      setPdf('');
      await load();
    } catch (error) {
      setMsg(error instanceof Error ? error.message : 'Unable to submit valuation.');
    } finally {
      setBusy(false);
    }
  }

  function signOut() {
    if (pdf) URL.revokeObjectURL(pdf);
    setPdf('');
    setToken('');
    setActive(null);
    setScripts([]);
    setOtpStage('email');
    setOtpRequested(false);
    setOtp('');
    setMsg('');
  }

  const total = questions.reduce((sum, question) => sum + Number(marks[question.id] || 0), 0);
  if (!token) return <main className="login"><form className="loginCard" onSubmit={otpRequested ? verifyCode : sendCodes}>
    <div className="logo">SD</div><h1>SHIVASHIRWAD</h1><p>Digital Valuation System</p>
    <label>{L.email}<input type="email" value={email} onChange={event => setEmail(event.target.value)} required disabled={otpRequested} autoComplete="email" /></label>
    {otpRequested && <><p>{L.otpHint}</p><label>{otpStage === 'email' ? L.emailOtp : L.phoneOtp}<input inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="[0-9]{6}" value={otp} onChange={event => setOtp(event.target.value.replace(/\D/g, '').slice(0, 6))} required /></label></>}
    <button className="primary full" disabled={busy}>{busy ? 'Please wait…' : otpRequested ? (otpStage === 'email' ? L.verifyEmail : L.verifyPhone) : L.send}</button>
    {otpRequested && <button type="button" className="link" onClick={() => { setOtpRequested(false); setOtp(''); setMsg(''); }}>Change email / request new codes</button>}
    <button type="button" className="link" onClick={() => setLang(lang === 'en' ? 'kn' : 'en')}>{L.lang}</button>
    {msg && <p className="error">{msg}</p>}
  </form></main>;

  const pending = scripts.filter(script => script.status !== 'SUBMITTED').length;
  const submitted = scripts.length - pending;
  return <div className="app"><aside className="sidebar"><div className="brand">SDVS<small>SHIVASHIRWAD</small></div>
    <nav><span>OVERVIEW</span><button onClick={() => { setActive(null); load(); }}>▦ Dashboard</button></nav>
    <div className="sideBottom">Secure examination workspace</div></aside>
    <section className="main"><header><span>SHIVASHIRWAD / {active ? 'VALUATION' : 'DASHBOARD'}</span>
      <div><button className="link" onClick={() => setLang(lang === 'en' ? 'kn' : 'en')}>{L.lang}</button><button className="link" onClick={signOut}>{L.signOut}</button></div>
    </header>
    {msg && <div className="notice" role="status">{msg}<button onClick={() => setMsg('')}>×</button></div>}
    {active ? <><div className="pageTop"><div><h1>{L.open}</h1><p>{L.warning}</p></div><span className="anonymous">◉ {active.anonymous_code}</span></div>
      <div className="workspace"><div className="panel"><b>Anonymous answer script · {active.page_count} pages</b>{pdf && <iframe title="Anonymous script" src={pdf} />}</div>
        <div className="panel">{questions.map(question => <article className="question" key={question.id}>
          <b>{L.q} {question.question_number}</b><small>{L.max}: {question.max_marks}</small>
          <label>{L.marks}<input type="number" min="0" max={question.max_marks} step="0.5" value={marks[question.id] ?? ''} onChange={event => setMarks({ ...marks, [question.id]: event.target.value })} /></label>
          <label>{L.remark}<textarea value={remarks[question.id] || ''} onChange={event => setRemarks({ ...remarks, [question.id]: event.target.value })} /></label>
          <details><summary>{L.private}</summary><textarea value={notes[question.id] || ''} onChange={event => setNotes({ ...notes, [question.id]: event.target.value })} /></details>
        </article>)}
        <div className="total">Calculated total <b>{total}</b></div><div className="buttons">
          <button className="secondary" disabled={busy} onClick={saveDraft}>{L.save}</button>
          <button className="primary" disabled={busy} onClick={submitEvaluation}>{L.submit}</button>
        </div></div></div></> : <>
      <div className="pageTop"><div><h1>{L.dashboard}</h1><p>{L.sub}</p></div><span className="secure">● {L.secure}</span></div>
      <div className="stats"><article><small>{L.scriptsCount}</small><strong>{scripts.length}</strong></article>
        <article><small>{L.pending}</small><strong>{pending}</strong></article>
        <article><small>{L.completed}</small><strong>{submitted}</strong></article></div>
      <section className="panel"><h2>{L.assigned}</h2>{scripts.length ? scripts.map(script => <div className="scriptRow" key={script.script_id}>
        <div><b>{script.anonymous_code}</b><small>{script.subject_name} · {script.page_count} pages · {script.status}</small></div>
        <button className="primary" disabled={script.status === 'SUBMITTED'} onClick={() => openScript(script)}>{L.open}</button>
      </div>) : <div className="empty">{L.noScripts}</div>}</section>
    </>}</section></div>;
}
