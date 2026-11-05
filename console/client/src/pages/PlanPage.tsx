// Tomorrow's plan: what the picker will send, approve or pause each nudge, and build segments instantly from Lakebase.
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@databricks/appkit-ui/react';
import { useMemo, useState } from 'react';
import { EmptyNote, ErrorNote, Loading, Source } from '../components/states';
import { postJson, useApi } from '../lib/api';
import { dec, hour, int, label, pct, when } from '../lib/format';

interface NudgeRow {
  nudge_type: string; sends: number; avg_uplift_pts: number; expected_extra_comebacks: number;
  copy_passed: number; copy_needs_review: number; decision_date: string;
  control_status: string | null; control_reviewer: string | null; control_updated_at: string | null;
}
interface Plan { by_nudge: NudgeRow[]; holds: { hold_reason: string; members: number }[]; ms: number }
interface SegmentRow {
  member_id: string; action: string; hold_reason: string | null; nudge_type: string | null; send_hour_local: number | null;
  estimated_uplift_pts: number | null; copy_status: string | null; age_band: string; market_size: string; plan: string;
  days_away_bucket: string; risk_band: string | null; lapse_risk: number | null;
}
interface Segments {
  totals: { members: number; sends: number; mean_lapse_risk: number | null; expected_extra_comebacks: number | null };
  rows: SegmentRow[]; ms: number; filters: number;
}

const HOLD_TEXT: Record<string, string> = {
  active: 'Used the app in the last 2 days',
  cooldown: 'Nudged in the last 72 hours',
  likely_back: 'Low lapse risk, likely back anyway',
  not_worth_it: 'No nudge beats its opt-out risk',
};
const FILTERS: { key: string; title: string }[] = [
  { key: 'action', title: 'Action' },
  { key: 'nudge_type', title: 'Nudge' },
  { key: 'risk_band', title: 'Lapse risk' },
  { key: 'age_band', title: 'Age band' },
  { key: 'market_size', title: 'Market' },
  { key: 'plan', title: 'Plan' },
  { key: 'days_away_bucket', title: 'Days away' },
  { key: 'tenure_group', title: 'Tenure' },
];

function ControlButtons({ row, onChange }: { row: NudgeRow; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (status: 'approved' | 'paused') => {
    setBusy(true);
    setErr(null);
    postJson('/api/plan/controls', { decision_date: row.decision_date, nudge_type: row.nudge_type, status })
      .then(onChange)
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  };
  return (
    <div className="flex items-center gap-2 justify-end">
      {row.control_status === 'approved' && <Badge className="bg-success text-success-foreground">Approved</Badge>}
      {row.control_status === 'paused' && <Badge variant="destructive">Paused</Badge>}
      {!row.control_status && <Badge variant="outline">Not reviewed</Badge>}
      <Button size="sm" variant="outline" disabled={busy || row.control_status === 'approved'} onClick={() => set('approved')}>Approve</Button>
      <Button size="sm" variant="outline" disabled={busy || row.control_status === 'paused'} onClick={() => set('paused')}>Pause</Button>
      {err && <span className="text-xs text-destructive">{err}</span>}
    </div>
  );
}

export function PlanPage() {
  const plan = useApi<Plan>('/api/plan');
  const options = useApi<Record<string, string[]>>('/api/segments/options');
  const [filters, setFilters] = useState<Record<string, string>>({ action: 'send' });
  const qs = useMemo(() => new URLSearchParams(Object.entries(filters).filter(([, v]) => v && v !== 'all')).toString(), [filters]);
  const seg = useApi<Segments>(`/api/segments?${qs}`);

  const totals = useMemo(() => {
    const rows = plan.data?.by_nudge ?? [];
    return {
      sends: rows.reduce((s, r) => s + r.sends, 0),
      extra: rows.filter((r) => r.control_status !== 'paused').reduce((s, r) => s + Number(r.expected_extra_comebacks), 0),
      paused: rows.filter((r) => r.control_status === 'paused').reduce((s, r) => s + r.sends, 0),
      holds: (plan.data?.holds ?? []).reduce((s, r) => s + r.members, 0),
      date: rows[0]?.decision_date,
    };
  }, [plan.data]);

  return (
    <div className="space-y-6 w-full max-w-7xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-foreground">
          {plan.data ? `Tomorrow: ${int(totals.sends - totals.paused)} pushes to ${pct((totals.sends - totals.paused) / Math.max(totals.sends + totals.holds, 1))} of reachable members, about ${int(totals.extra)} expected extra comebacks` : "Tomorrow's plan"}
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          The picker sends only where the estimated uplift of a nudge beats its opt-out risk. Pausing a nudge stops the notification service from sending it.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Planned sends by nudge</CardTitle>
          <CardDescription>For {totals.date ?? 'the next send day'}. Approve each nudge, or pause it for the day.</CardDescription>
        </CardHeader>
        <CardContent>
          {plan.loading && !plan.data && <Loading rows={5} />}
          {plan.error && <ErrorNote message={plan.error} />}
          {plan.data && plan.data.by_nudge.length === 0 && <EmptyNote title="No sends planned">The picker held every member for this day.</EmptyNote>}
          {plan.data && plan.data.by_nudge.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Nudge</TableHead>
                  <TableHead className="text-right">Sends</TableHead>
                  <TableHead className="text-right">Avg estimated uplift</TableHead>
                  <TableHead className="text-right">Expected extra comebacks</TableHead>
                  <TableHead className="text-right">Copy</TableHead>
                  <TableHead className="text-right">Review</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {plan.data.by_nudge.map((r) => (
                  <TableRow key={r.nudge_type}>
                    <TableCell className="font-medium">{label(r.nudge_type)}</TableCell>
                    <TableCell className="text-right tabular-nums">{int(r.sends)}</TableCell>
                    <TableCell className="text-right tabular-nums">+{dec(r.avg_uplift_pts)} pts</TableCell>
                    <TableCell className="text-right tabular-nums">{int(r.expected_extra_comebacks)}</TableCell>
                    <TableCell className="text-right">
                      {r.copy_needs_review > 0 ? <span className="text-warning">{r.copy_needs_review} need review</span> : <span className="text-success">all passed</span>}
                    </TableCell>
                    <TableCell><ControlButtons row={r} onChange={plan.reload} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <Source>
            Lakebase hearth.next_best_action, answered in {plan.data ? `${plan.data.ms} ms` : '-'}. Reviews are saved to console.campaign_controls and copied to Delta daily.
          </Source>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Why the other {int(totals.holds)} members are held</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {(plan.data?.holds ?? []).map((h) => (
            <div key={h.hold_reason}>
              <p className="text-2xl font-semibold tabular-nums">{int(h.members)}</p>
              <p className="text-sm text-muted-foreground">{HOLD_TEXT[h.hold_reason] ?? label(h.hold_reason)}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Build a segment</CardTitle>
          <CardDescription>Counts update as you pick. No warehouse query and no overnight export: this reads Lakebase directly.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
            {FILTERS.map((f) => (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`f-${f.key}`}>{f.title}</Label>
                <Select value={filters[f.key] ?? 'all'} onValueChange={(v) => setFilters((p) => ({ ...p, [f.key]: v }))}>
                  <SelectTrigger id={`f-${f.key}`}><SelectValue placeholder="All" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All</SelectItem>
                    {(options.data?.[f.key] ?? []).map((v) => <SelectItem key={v} value={v}>{label(v)}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
          {seg.error && <ErrorNote message={seg.error} />}
          {seg.data && (
            <div className="flex flex-wrap gap-6 items-end">
              <div><p className="text-3xl font-semibold tabular-nums">{int(seg.data.totals.members)}</p><p className="text-sm text-muted-foreground">members</p></div>
              <div><p className="text-3xl font-semibold tabular-nums">{int(seg.data.totals.sends)}</p><p className="text-sm text-muted-foreground">planned sends</p></div>
              <div><p className="text-3xl font-semibold tabular-nums">{pct(seg.data.totals.mean_lapse_risk)}</p><p className="text-sm text-muted-foreground">average lapse risk</p></div>
              <div><p className="text-3xl font-semibold tabular-nums">{int(seg.data.totals.expected_extra_comebacks)}</p><p className="text-sm text-muted-foreground">expected extra comebacks</p></div>
              <Badge variant="secondary" className="ml-auto">answered in {seg.data.ms} ms</Badge>
            </div>
          )}
          {seg.loading && !seg.data && <Loading rows={4} />}
          {seg.data && seg.data.rows.length === 0 && <EmptyNote title="No members match">Loosen a filter.</EmptyNote>}
          {seg.data && seg.data.rows.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Member</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Send at</TableHead>
                  <TableHead>Age</TableHead>
                  <TableHead>Market</TableHead>
                  <TableHead>Plan</TableHead>
                  <TableHead>Days away</TableHead>
                  <TableHead className="text-right">Lapse risk</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {seg.data.rows.map((r) => (
                  <TableRow key={r.member_id}>
                    <TableCell className="font-mono text-xs">{r.member_id}</TableCell>
                    <TableCell>{r.action === 'send' ? label(r.nudge_type) : <span className="text-muted-foreground">hold: {label(r.hold_reason)}</span>}</TableCell>
                    <TableCell>{r.send_hour_local != null ? `${hour(r.send_hour_local)} local` : '-'}</TableCell>
                    <TableCell>{r.age_band}</TableCell>
                    <TableCell>{r.market_size}</TableCell>
                    <TableCell>{r.plan}</TableCell>
                    <TableCell>{r.days_away_bucket}</TableCell>
                    <TableCell className="text-right tabular-nums">{pct(r.lapse_risk)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          <Source>
            First 50 members, riskiest first. Lakebase hearth.member_segments joined to hearth.next_best_action. No PII or sensitive attributes are synced.
            {plan.data?.by_nudge[0]?.control_updated_at && ` Last review ${when(plan.data.by_nudge[0].control_updated_at)}.`}
          </Source>
        </CardContent>
      </Card>
    </div>
  );
}
