// Copy review: messages that failed the automatic check come first. A marketer can edit, re-run the governed check,
// and approve or reject. Approval re-runs the check on the server, so only passing copy can be approved.
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from '@databricks/appkit-ui/react';
import { useState } from 'react';
import { EmptyNote, ErrorNote, Loading, Source } from '../components/states';
import { ApiError, postJson, useApi } from '../lib/api';
import { hour, int, label, when } from '../lib/format';

interface CopyRow {
  member_id: string; decision_date: string; nudge_type: string; copy_text: string | null; copy_status: string;
  send_hour_local: number | null; timezone: string; estimated_uplift_pts: number; risk_band: string | null;
  review_decision: string | null; reviewed_copy: string | null; reviewer: string | null; reviewed_at: string | null;
}
interface CopyList { rows: CopyRow[]; counts: { copy_status: string; messages: number }[]; ms: number }
interface Check {
  brief: string; passed: boolean; reasons: string[]; ms: number;
  scores: Record<'personal_info' | 'sensitive_trait' | 'unsupported_claim' | 'guilt_or_pressure' | 'on_topic' | 'tone', number | null>;
}

const REASON_TEXT: Record<string, string> = {
  empty: 'No message',
  too_long: 'Over 90 characters',
  not_one_line: 'More than one line',
  digits_or_links: 'Has digits, @, # or a link',
  check_failed: 'The check could not run',
  personal_info: 'Mentions personal information',
  sensitive_trait: 'Mentions a sensitive trait',
  unsupported_claim: 'Claims something the facts do not support',
  guilt_or_pressure: 'Guilt or pressure',
  off_topic: 'Not about this nudge',
  off_brand: 'Off-brand tone',
};

function CopyCard({ row, onDone }: { row: CopyRow; onDone: () => void }) {
  const [text, setText] = useState(row.reviewed_copy ?? row.copy_text ?? '');
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState<'check' | 'approved' | 'rejected' | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const runCheck = () => {
    setBusy('check');
    setErr(null);
    postJson<Check>('/api/copy/check', { member_id: row.member_id, copy: text })
      .then(setCheck)
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(null));
  };
  const review = (decision: 'approved' | 'rejected') => {
    setBusy(decision);
    setErr(null);
    postJson<{ check: Check | null }>('/api/copy/review', { member_id: row.member_id, copy: text, decision })
      .then((r) => {
        if (r.check) setCheck({ ...r.check, ms: 0 });
        onDone();
      })
      .catch((e: Error) => {
        setErr(e.message);
        if (e instanceof ApiError && e.body && typeof e.body === 'object' && 'check' in e.body) {
          setCheck((e.body as { check: Check }).check);
        }
      })
      .finally(() => setBusy(null));
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <CardTitle className="text-base">{label(row.nudge_type)}</CardTitle>
          <span className="font-mono text-xs text-muted-foreground">{row.member_id}</span>
          {row.copy_status === 'needs_review' ? <Badge className="bg-warning text-warning-foreground">Failed the check</Badge> : <Badge variant="secondary">Passed the check</Badge>}
          {row.review_decision === 'approved' && <Badge className="bg-success text-success-foreground">Approved by {row.reviewer}</Badge>}
          {row.review_decision === 'rejected' && <Badge variant="destructive">Rejected by {row.reviewer}</Badge>}
        </div>
        <CardDescription>
          Sends at {hour(row.send_hour_local)} {row.timezone.replace('America/', '').replace('_', ' ')} time. Estimated uplift +{row.estimated_uplift_pts} pts.
          {row.reviewed_at && ` Reviewed ${when(row.reviewed_at)}.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor={`copy-${row.member_id}`}>Message ({text.length} of 90 characters)</Label>
          <Textarea id={`copy-${row.member_id}`} value={text} rows={2} onChange={(e) => { setText(e.target.value); setCheck(null); }} />
        </div>
        <div className="flex gap-2 flex-wrap">
          <Button size="sm" variant="outline" disabled={busy !== null || !text.trim()} onClick={runCheck}>{busy === 'check' ? 'Checking…' : 'Run the check'}</Button>
          <Button size="sm" disabled={busy !== null || !text.trim()} onClick={() => review('approved')}>{busy === 'approved' ? 'Checking and saving…' : 'Approve'}</Button>
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => review('rejected')}>Reject</Button>
        </div>
        {err && <ErrorNote title="Not saved" message={err} />}
        {check && (
          <div className={`rounded-md p-3 text-sm ${check.passed ? 'bg-success/10' : 'bg-destructive/10'}`}>
            <p className="font-medium">{check.passed ? 'Passes the check' : 'Fails the check'}{check.ms ? ` (${(check.ms / 1000).toFixed(1)} s)` : ''}</p>
            {check.reasons.length > 0 && <ul className="list-disc ml-5">{check.reasons.map((r) => <li key={r}>{REASON_TEXT[r] ?? r}</li>)}</ul>}
            <p className="text-xs text-muted-foreground mt-2">Facts this message may use: {check.brief}</p>
            <p className="text-xs text-muted-foreground">
              OpenJev scores: personal info {check.scores.personal_info}, sensitive trait {check.scores.sensitive_trait}, unsupported claim {check.scores.unsupported_claim},
              pressure {check.scores.guilt_or_pressure}, on topic {check.scores.on_topic}, tone {check.scores.tone} of 2
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function CopyPage() {
  const [status, setStatus] = useState('needs_review');
  const [nudge, setNudge] = useState('all');
  const list = useApi<CopyList>(`/api/copy?status=${status}&nudge_type=${nudge}&limit=24`);
  const count = (s: string) => list.data?.counts.find((c) => c.copy_status === s)?.messages ?? 0;

  return (
    <div className="space-y-6 w-full max-w-5xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-foreground">
          {list.data ? `${int(count('needs_review'))} messages need a person, ${int(count('passed'))} passed the automatic check` : 'Copy review'}
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          Claude Haiku wrote each message from nudge facts only. OpenJev checked it for personal information, sensitive traits, unsupported claims, pressure, topic and tone.
          Messages that fail are never sent until someone approves a version that passes.
        </p>
      </div>
      <div className="flex gap-3 items-end">
        <div className="space-y-1">
          <Label htmlFor="status">Show</Label>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger id="status" className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="needs_review">Failed the check</SelectItem>
              <SelectItem value="passed">Passed the check</SelectItem>
              <SelectItem value="all">All sends</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="nudge">Nudge</Label>
          <Select value={nudge} onValueChange={setNudge}>
            <SelectTrigger id="nudge" className="w-48"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All nudges</SelectItem>
              {['new_likes', 'match_waiting', 'profile_tip', 'new_people_nearby', 'we_miss_you'].map((n) => <SelectItem key={n} value={n}>{label(n)}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      {list.loading && !list.data && <Loading rows={6} />}
      {list.error && <ErrorNote message={list.error} />}
      {list.data && list.data.rows.length === 0 && <EmptyNote title="Nothing to review">Every message for this filter passed the check or was reviewed.</EmptyNote>}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {list.data?.rows.map((r) => <CopyCard key={`${r.member_id}-${r.review_decision ?? ''}`} row={r} onDone={list.reload} />)}
      </div>
      <Source>Lakebase hearth.next_best_action, answered in {list.data ? `${list.data.ms} ms` : '-'}. The check runs hearth_ml.check_nudge_copy on the SQL warehouse, the same function as the daily job.</Source>
    </div>
  );
}
