// Overview: how push campaigns are doing, from the governed metric views. Headline first, then the evidence.
import {
  BarChart,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useAnalyticsQuery,
} from '@databricks/appkit-ui/react';
import { useMemo } from 'react';
import { EmptyNote, ErrorNote, Loading, Source } from '../components/states';
import { day, dec, int, label, pct, pts } from '../lib/format';

const BLAST_CHANGE = '2026-10-01';
const shortDay = (v: unknown) => day(v).replace(/, \d{4}$/, '');

function Tile({ title, value, unit, delta, deltaGood, note }: {
  title: string; value: string; unit: string; delta?: string; deltaGood?: boolean; note: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{title}</CardDescription>
        <CardTitle className="text-3xl tabular-nums">
          {value} <span className="text-sm font-normal text-muted-foreground">{unit}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {delta && <p className={`text-sm font-medium ${deltaGood ? 'text-success' : 'text-destructive'}`}>{delta}</p>}
        <Source>{note}</Source>
      </CardContent>
    </Card>
  );
}

export function OverviewPage() {
  const weekly = useAnalyticsQuery('weekly_reach', {});
  const effects = useAnalyticsQuery('nudge_effects', {});
  const blast = useAnalyticsQuery('blast_vs_test', {});
  const cohorts = useAnalyticsQuery('lapse_cohorts', {});
  const fresh = useAnalyticsQuery('data_freshness', {});

  const summary = useMemo(() => {
    const rows = weekly.data ?? [];
    if (rows.length < 4) return null;
    const full = rows.slice(1, -1); // the first and last weeks are partial
    const before = full.filter((r) => String(r.week).slice(0, 10) < '2026-09-28');
    const after = full.filter((r) => String(r.week).slice(0, 10) >= '2026-10-05');
    const avg = (xs: typeof full, k: 'opt_outs' | 'pushes_per_active_member') =>
      xs.reduce((s, r) => s + Number(r[k]), 0) / Math.max(xs.length, 1);
    return {
      reachStart: Number(full[0].reachable_end),
      reachNow: Number(rows[rows.length - 1].reachable_end),
      optBefore: avg(before, 'opt_outs'),
      optAfter: avg(after, 'opt_outs'),
      firstWeek: full[0].week,
      lastWeek: rows[rows.length - 1].week,
    };
  }, [weekly.data]);

  const extraTotal = useMemo(
    () => (effects.data ?? []).reduce((s, r) => s + Number(r.extra_returns), 0),
    [effects.data],
  );
  const topTwo = useMemo(() => {
    const rows = [...(effects.data ?? [])].sort((a, b) => Number(b.extra_returns) - Number(a.extra_returns));
    return rows.length >= 2 ? `${label(rows[0].nudge_type)} and ${label(rows[1].nudge_type)}` : null;
  }, [effects.data]);
  const byType = useMemo(() => Object.fromEntries((blast.data ?? []).map((r) => [r.campaign_type, r])), [blast.data]);
  const through = fresh.data?.[0];
  const sourceNote = (view: string) =>
    `${view} metric view${through ? `, data through ${day(through.decisions_through)}` : ''}`;

  return (
    <div className="space-y-6 w-full max-w-7xl mx-auto">
      <div>
        <h2 className="text-2xl font-bold text-foreground">
          {summary
            ? `Opt-outs ran ${dec(summary.optAfter / summary.optBefore, 1)}x higher after the blast tripled, and reach fell by ${int(summary.reachStart - summary.reachNow)} members, while the nudge test added ${int(extraTotal)} returns over its holdout`
            : 'Campaign overview'}
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          The blast went from weekly to three times a week on {day(BLAST_CHANGE)}. Uplift comes from the randomized nudge test against its holdout.
        </p>
      </div>

      {weekly.error && <ErrorNote message={weekly.error} />}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {weekly.loading || !summary ? (
          Array.from({ length: 4 }, (_, i) => (
            <Card key={i}><CardContent className="pt-6"><Loading rows={2} /></CardContent></Card>
          ))
        ) : (
          <>
            <Tile title="Reachable by push, latest week" value={int(summary.reachNow)} unit="members"
              delta={`${int(summary.reachNow - summary.reachStart)} since the week of ${day(summary.firstWeek)}`} deltaGood={false}
              note={sourceNote('reach_metrics')} />
            <Tile title="Opt-outs per week, after the blast change" value={int(summary.optAfter)} unit="per week"
              delta={`${dec(summary.optAfter / summary.optBefore, 1)}x the ${int(summary.optBefore)} per week before`} deltaGood={false}
              note="Full weeks only, before 2026-09-28 against from 2026-10-05. reach_metrics" />
            <Tile title="Extra returns from the nudge test" value={int(extraTotal)} unit="members"
              delta="Came back because of a randomly assigned push, over the holdout" deltaGood
              note={sourceNote('campaign_metrics, nudge test only,')} />
            <Tile title="Opt-outs per push, blast against test" value={pct(byType.blast?.opt_out_rate, 2)} unit="blast"
              delta={byType.nudge_test ? `${pct(byType.nudge_test.opt_out_rate, 2)} for the nudge test` : undefined}
              deltaGood={false} note={sourceNote('campaign_metrics')} />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>Opt-outs jumped when blast volume tripled</CardTitle>
            <CardDescription>Per week, each on its own scale. The first and last weeks are partial.</CardDescription>
          </CardHeader>
          <CardContent>
            {weekly.loading && <Loading rows={6} />}
            {weekly.data && weekly.data.length > 0 && (
              <div className="space-y-1">
                <p className="text-sm font-medium">Opt-outs</p>
                <BarChart
                  data={weekly.data.map((r) => ({ week: shortDay(r.week), opt_outs: Number(r.opt_outs) }))}
                  xKey="week" yKey="opt_outs" orientation="vertical" height={160} colorPalette="categorical"
                  ariaLabel="Opt-outs per week" />
                <p className="text-sm font-medium">Blast pushes</p>
                <BarChart
                  data={weekly.data.map((r) => ({ week: shortDay(r.week), blast_pushes: Number(r.blast_pushes) }))}
                  xKey="week" yKey="blast_pushes" orientation="vertical" height={160} colorPalette="categorical"
                  ariaLabel="Blast pushes per week" />
              </div>
            )}
            {weekly.data && weekly.data.length === 0 && <EmptyNote title="No weeks yet" />}
            <Source>reach_metrics metric view</Source>
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{topTwo ? `${topTwo} bring back the most members` : 'Extra returns by nudge'}</CardTitle>
            <CardDescription>Extra returns vs holdout by nudge, whole test. Negative means the push did harm.</CardDescription>
          </CardHeader>
          <CardContent>
            {effects.loading && <Loading rows={6} />}
            {effects.error && <ErrorNote message={effects.error} />}
            {effects.data && effects.data.length > 0 && (
              <BarChart
                data={effects.data.map((r) => ({ nudge: label(r.nudge_type), extra_returns: Math.round(Number(r.extra_returns)) }))}
                xKey="nudge" yKey="extra_returns" orientation="horizontal" height={280} colorPalette="diverging"
                ariaLabel="Extra returns vs holdout by nudge type" />
            )}
            <Source>{sourceNote('campaign_metrics')}</Source>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Each nudge against its holdout</CardTitle>
            <CardDescription>Uplift is percentage points of 72-hour return rate.</CardDescription>
          </CardHeader>
          <CardContent>
            {effects.loading && <Loading />}
            {effects.data && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Nudge</TableHead>
                    <TableHead className="text-right">Pushes sent</TableHead>
                    <TableHead className="text-right">Uplift</TableHead>
                    <TableHead className="text-right">Extra returns</TableHead>
                    <TableHead className="text-right">Opt-outs per push</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {effects.data.map((r) => (
                    <TableRow key={r.nudge_type}>
                      <TableCell>{label(r.nudge_type)}</TableCell>
                      <TableCell className="text-right tabular-nums">{int(r.pushes_sent)}</TableCell>
                      <TableCell className={`text-right tabular-nums ${Number(r.uplift_pts) < 0 ? 'text-destructive' : ''}`}>{pts(r.uplift_pts)}</TableCell>
                      <TableCell className="text-right tabular-nums">{int(r.extra_returns)}</TableCell>
                      <TableCell className="text-right tabular-nums">{pct(r.opt_out_rate, 2)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <Source>{sourceNote('campaign_metrics')}</Source>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Lapse risk rises with age and is lower on paid plans</CardTitle>
            <CardDescription>Members scored on {day(through?.scores_through)}, by age band and plan.</CardDescription>
          </CardHeader>
          <CardContent>
            {cohorts.loading && <Loading />}
            {cohorts.error && <ErrorNote message={cohorts.error} />}
            {cohorts.data && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Age band</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead className="text-right">Members</TableHead>
                    <TableHead className="text-right">Average risk</TableHead>
                    <TableHead className="text-right">High risk</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {cohorts.data.map((r) => (
                    <TableRow key={`${r.age_band}-${r.plan}`}>
                      <TableCell>{r.age_band}</TableCell>
                      <TableCell>{r.plan}</TableCell>
                      <TableCell className="text-right tabular-nums">{int(r.members)}</TableCell>
                      <TableCell className="text-right tabular-nums">{pct(r.mean_lapse_risk)}</TableCell>
                      <TableCell className="text-right tabular-nums">{int(r.high_risk_members)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
            <Source>lapse_risk_metrics metric view, scored by the lapse model</Source>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
