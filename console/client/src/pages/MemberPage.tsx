// Member lookup: one member's next action by id, the same read the notification service makes, with its latency.
// A member who isn't in the plan yet, like a signup from today, can be scored live on the hearth-lapse endpoint.
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useServingInvoke,
} from '@databricks/appkit-ui/react';
import { useState } from 'react';
import { ErrorNote, Source } from '../components/states';
import { getJson } from '../lib/api';
import { dec, hour, int, label, pct, when } from '../lib/format';

interface Member {
  member_id: string; decision_date: string; action: string; hold_reason: string | null; nudge_type: string | null;
  send_at_utc: string | null; send_hour_local: number | null; timezone: string; estimated_uplift_pts: number | null;
  net_value_pts: number | null; lapse_risk: number | null; risk_band: string | null; copy_text: string | null;
  copy_status: string | null; policy_version: string; age_band: string | null; market_size: string | null;
  platform: string | null; plan: string | null; tenure_days: number | null; days_away: number | null;
  days_away_bucket: string | null; pushes_7d: number | null; preferred_hour_local: number | null;
  hour_source: string | null; segments_as_of: string | null; control_status: string | null;
  review_decision: string | null; reviewed_copy: string | null; reviewer: string | null; reviewed_at: string | null;
}

function Field({ name, value }: { name: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{name}</p>
      <p className="font-medium">{value}</p>
    </div>
  );
}

function Lookup() {
  const [id, setId] = useState('m_000001');
  const [member, setMember] = useState<Member | null>(null);
  const [ms, setMs] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const find = () => {
    setBusy(true);
    setErr(null);
    setMember(null);
    const t = performance.now();
    getJson<{ member: Member; ms: number }>(`/api/members/${encodeURIComponent(id.trim())}`)
      .then((r) => { setMember(r.member); setMs(r.ms); })
      .catch((e: Error) => setErr(e.message))
      .finally(() => { setBusy(false); setMs((m) => m ?? Math.round(performance.now() - t)); });
  };
  const m = member;
  const finalCopy = m?.review_decision === 'approved' ? m.reviewed_copy : m?.copy_text;
  const willSend = m?.action === 'send' && m.control_status !== 'paused' && m.review_decision !== 'rejected'
    && (m.copy_status === 'passed' || m.review_decision === 'approved');

  return (
    <Card>
      <CardHeader>
        <CardTitle>Look up a member</CardTitle>
        <CardDescription>The notification service makes this same read by member id just before a push goes out.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form className="flex gap-2 items-end" onSubmit={(e) => { e.preventDefault(); find(); }}>
          <div className="space-y-1">
            <Label htmlFor="member-id">Member id</Label>
            <Input id="member-id" value={id} onChange={(e) => setId(e.target.value)} className="w-48 font-mono" />
          </div>
          <Button type="submit" disabled={busy}>{busy ? 'Looking up…' : 'Look up'}</Button>
          {ms != null && <Badge variant="secondary">Lakebase answered in {ms} ms</Badge>}
        </form>
        {err && <ErrorNote title="Not found" message={err} />}
        {m && (
          <div className="space-y-4">
            <div className={`rounded-md p-4 ${willSend ? 'bg-success/10' : 'bg-muted'}`}>
              <p className="text-lg font-semibold">
                {m.action === 'send'
                  ? `${willSend ? 'Send' : 'Planned, but held back:'} ${label(m.nudge_type)} at ${hour(m.send_hour_local)} local (${when(m.send_at_utc)})`
                  : `Hold: ${label(m.hold_reason)}`}
              </p>
              {m.action === 'send' && !willSend && (
                <p className="text-sm text-muted-foreground">
                  {m.control_status === 'paused' ? 'This nudge is paused for the day.' : m.review_decision === 'rejected' ? 'A marketer rejected this message.' : 'The message failed the copy check and is waiting for review.'}
                </p>
              )}
              {finalCopy && <p className="mt-2">“{finalCopy}”</p>}
              {m.review_decision && <p className="text-xs text-muted-foreground mt-1">{m.review_decision} by {m.reviewer} {when(m.reviewed_at)}</p>}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <Field name="Lapse risk" value={m.lapse_risk != null ? `${pct(m.lapse_risk)} (${m.risk_band})` : 'not scored, away too long'} />
              <Field name="Estimated uplift" value={m.estimated_uplift_pts != null ? `+${dec(m.estimated_uplift_pts)} pts` : '-'} />
              <Field name="Days away" value={m.days_away != null ? `${dec(m.days_away)} (${m.days_away_bucket})` : '-'} />
              <Field name="Pushes in 7 days" value={int(m.pushes_7d)} />
              <Field name="Age band, plan" value={`${m.age_band ?? '-'}, ${m.plan ?? '-'}`} />
              <Field name="Market, platform" value={`${m.market_size ?? '-'}, ${m.platform ?? '-'}`} />
              <Field name="Usual hour" value={`${hour(m.preferred_hour_local)} (${m.hour_source ?? '-'})`} />
              <Field name="Tenure" value={m.tenure_days != null ? `${int(m.tenure_days)} days` : '-'} />
            </div>
            <Source>Lakebase hearth.next_best_action and hearth.member_segments, for {m.decision_date}, picker {m.policy_version}. No names or contact details are in Lakebase.</Source>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function lapseRisk(data: unknown): number | null {
  if (!data || typeof data !== 'object' || !('predictions' in data)) return null;
  const p: unknown = (data as { predictions: unknown }).predictions;
  if (!Array.isArray(p) || !Array.isArray(p[0])) return null;
  const v: unknown = (p[0] as unknown[])[1];
  return typeof v === 'number' ? v : null;
}

const NEW_MEMBER_BASE = {
  market_size: 'large', metro: 'Chicago', sessions_1d: 0, sessions_prev_7d: 0, push_sessions_28d: 0,
  likes_received_7d: 1, matches_14d: 0, messages_sent_7d: 0, messages_sent_prev_7d: 0, messages_sent_14d: 0,
  messages_received_14d: 0, reply_ratio_14d: 0, pushes_7d: 0, pushes_28d: 0, open_rate_28d: 0, reachable: true,
};

function NewMember() {
  const [age, setAge] = useState('25-29');
  const [platform, setPlatform] = useState('ios');
  const [sessions, setSessions] = useState('1');
  const [views, setViews] = useState('9');
  const [likes, setLikes] = useState('3');
  const { invoke, data, loading, error } = useServingInvoke({});
  const score = () => {
    const s = Number(sessions) || 0;
    void invoke({
      dataframe_records: [{
        ...NEW_MEMBER_BASE, plan: 'free', age_band: age, platform, tenure_days: 1, days_since_last_session: 1,
        sessions_7d: s, sessions_trend_7d: s, sessions_14d: s, sessions_28d: s,
        profile_views_7d: Number(views) || 0, likes_sent_7d: Number(likes) || 0,
      }],
    });
  };
  // The endpoint returns predict_proba: [[chance of staying, chance of lapsing]] per member
  const risk = lapseRisk(data);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Score a brand-new member live</CardTitle>
        <CardDescription>Members who signed up today are not in the plan yet. Their lapse risk comes from the hearth-lapse endpoint, run as you.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="space-y-1">
            <Label htmlFor="nm-age">Age band</Label>
            <Select value={age} onValueChange={setAge}>
              <SelectTrigger id="nm-age"><SelectValue /></SelectTrigger>
              <SelectContent>{['18-24', '25-29', '30-34', '35-44', '45+'].map((a) => <SelectItem key={a} value={a}>{a}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="nm-platform">Platform</Label>
            <Select value={platform} onValueChange={setPlatform}>
              <SelectTrigger id="nm-platform"><SelectValue /></SelectTrigger>
              <SelectContent><SelectItem value="ios">ios</SelectItem><SelectItem value="android">android</SelectItem></SelectContent>
            </Select>
          </div>
          <div className="space-y-1"><Label htmlFor="nm-s">Sessions so far</Label><Input id="nm-s" type="number" min={0} value={sessions} onChange={(e) => setSessions(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="nm-v">Profiles viewed</Label><Input id="nm-v" type="number" min={0} value={views} onChange={(e) => setViews(e.target.value)} /></div>
          <div className="space-y-1"><Label htmlFor="nm-l">Likes sent</Label><Input id="nm-l" type="number" min={0} value={likes} onChange={(e) => setLikes(e.target.value)} /></div>
        </div>
        <div className="flex items-center gap-3">
          <Button onClick={score} disabled={loading}>{loading ? 'Scoring…' : 'Score'}</Button>
          {risk != null && <p className="text-lg"><span className="font-semibold">{pct(risk)}</span> chance of no session in the next 7 days</p>}
        </div>
        {error && <ErrorNote title="Scoring failed" message={error} />}
        <Source>hearth-lapse serving endpoint, the champion lapse model, with features sent in the request</Source>
      </CardContent>
    </Card>
  );
}

export function MemberPage() {
  return (
    <div className="space-y-6 w-full max-w-5xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-foreground">Member lookup</h2>
        <p className="text-sm text-muted-foreground mt-1">What Hearth will do for one member tomorrow, and why.</p>
      </div>
      <Lookup />
      <NewMember />
    </div>
  );
}
