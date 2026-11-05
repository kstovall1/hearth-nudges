// The lifecycle console's API. Reads the synced next-best-action and member-segment tables in Lakebase, and
// writes marketers' decisions to the console schema, which the app's service principal owns:
//   console.campaign_controls  approve or pause a nudge type for a decision date
//   console.copy_reviews       approve (with optional edits) or reject one member's message
// hearth-daily copies both back to Delta, and the notification service honors them at send time.
import type { Application, Request } from 'express';
import { z } from 'zod';
import { runStatement } from './warehouse';

interface Lakebase {
  query(text: string, params?: unknown[]): Promise<{ rows: Record<string, unknown>[] }>;
}
interface AppKitHandle {
  lakebase: Lakebase;
  server: { extend(fn: (app: Application) => void): void };
}

const CATALOG = process.env.HEARTH_CATALOG ?? 'katie_febar_catalog';
const NUDGES = ['new_likes', 'match_waiting', 'profile_tip', 'new_people_nearby', 'we_miss_you'] as const;

const SETUP_SQL = [
  'CREATE SCHEMA IF NOT EXISTS console',
  `CREATE TABLE IF NOT EXISTS console.campaign_controls (
     decision_date DATE NOT NULL,
     nudge_type TEXT NOT NULL,
     status TEXT NOT NULL CHECK (status IN ('approved', 'paused')),
     note TEXT,
     reviewer TEXT NOT NULL,
     updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
     PRIMARY KEY (decision_date, nudge_type)
   )`,
  `CREATE TABLE IF NOT EXISTS console.copy_reviews (
     review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
     member_id TEXT NOT NULL,
     decision_date DATE NOT NULL,
     nudge_type TEXT NOT NULL,
     original_copy TEXT,
     final_copy TEXT NOT NULL,
     decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
     check_passed BOOLEAN,
     check_reasons TEXT[],
     reviewer TEXT NOT NULL,
     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
   )`,
  'CREATE INDEX IF NOT EXISTS copy_reviews_member ON console.copy_reviews (decision_date, member_id, created_at DESC)',
  // The notification service and the copy-back job read these as other identities
  'GRANT USAGE ON SCHEMA console TO PUBLIC',
  'GRANT SELECT ON ALL TABLES IN SCHEMA console TO PUBLIC',
  'ALTER DEFAULT PRIVILEGES IN SCHEMA console GRANT SELECT ON TABLES TO PUBLIC',
];

function reviewer(req: Request): string {
  return req.header('x-forwarded-email') ?? req.header('x-forwarded-user') ?? 'local-developer';
}

async function timed<T>(fn: () => Promise<T>): Promise<{ result: T; ms: number }> {
  const t = performance.now();
  const result = await fn();
  return { result, ms: Math.round((performance.now() - t) * 10) / 10 };
}

const SEGMENT_FILTERS = {
  nudge_type: 'n.nudge_type',
  action: 'n.action',
  risk_band: 's.risk_band',
  age_band: 's.age_band',
  market_size: 's.market_size',
  plan: 's.plan',
  platform: 's.platform',
  tenure_group: 's.tenure_group',
  days_away_bucket: 's.days_away_bucket',
} as const;
type SegmentKey = keyof typeof SEGMENT_FILTERS;

const CheckBody = z.object({ member_id: z.string().regex(/^m_\d{6,7}$/), copy: z.string().trim().min(1).max(200) });
const ReviewBody = CheckBody.extend({ decision: z.enum(['approved', 'rejected']) });
const ControlBody = z.object({
  decision_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  nudge_type: z.enum(NUDGES),
  status: z.enum(['approved', 'paused']),
  note: z.string().max(300).optional(),
});

// The check scores edited copy against the facts the nudge was allowed to use, from the stored brief, so an
// edit can't change the facts. Same governed function as the daily job.
async function checkCopy(memberId: string, copy: string) {
  const rows = await runStatement(
    `SELECT c.decision_date, c.brief, k.passed, to_json(k.reasons) AS reasons, k.personal_info, k.sensitive_trait,
            k.unsupported_claim, k.guilt_or_pressure, k.on_topic, k.tone
     FROM (
       SELECT decision_date, brief, ${CATALOG}.hearth_ml.check_nudge_copy(brief, :copy) AS k
       FROM ${CATALOG}.hearth_gold.nudge_copy
       WHERE member_id = :member_id
         AND decision_date = (SELECT max(decision_date) FROM ${CATALOG}.hearth_gold.nudge_copy)
       QUALIFY row_number() OVER (ORDER BY attempt DESC) = 1
     ) c`,
    [
      { name: 'copy', value: copy },
      { name: 'member_id', value: memberId },
    ],
  );
  if (rows.length === 0) return null;
  const r = rows[0];
  const num = (v: string | null) => (v == null ? null : Math.round(Number(v) * 1000) / 1000);
  return {
    decision_date: r.decision_date,
    brief: r.brief,
    passed: r.passed === 'true',
    reasons: r.reasons ? (JSON.parse(r.reasons) as string[]) : [],
    scores: {
      personal_info: num(r.personal_info),
      sensitive_trait: num(r.sensitive_trait),
      unsupported_claim: num(r.unsupported_claim),
      guilt_or_pressure: num(r.guilt_or_pressure),
      on_topic: num(r.on_topic),
      tone: num(r.tone),
    },
  };
}

export async function setupConsole(appkit: AppKitHandle) {
  try {
    for (const statement of SETUP_SQL) await appkit.lakebase.query(statement);
    console.log('[console] schema console ready');
  } catch (err) {
    console.warn('[console] schema setup failed:', (err as Error).message);
  }
  const db = appkit.lakebase;

  appkit.server.extend((app) => {
    app.get('/api/whoami', (req, res) => {
      res.json({ email: req.header('x-forwarded-email') ?? null, user: req.header('x-forwarded-user') ?? null });
    });

    // What the serving tables hold right now, for freshness notes on every page
    app.get('/api/status', async (_req, res) => {
      try {
        const { result, ms } = await timed(() =>
          db.query(`SELECT max(decision_date)::text AS decision_date, max(decided_at) AS decided_at,
                           max(copy_checked_at) AS copy_checked_at, count(*)::int AS members,
                           count(*) FILTER (WHERE action = 'send')::int AS sends,
                           (SELECT max(as_of)::text FROM hearth.member_segments) AS segments_as_of
                    FROM hearth.next_best_action`),
        );
        res.json({ ...result.rows[0], ms });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    // Tomorrow's plan by nudge, with each nudge's approval state and copy status
    app.get('/api/plan', async (_req, res) => {
      try {
        const { result, ms } = await timed(async () => {
          const byNudge = await db.query(`
            SELECT n.nudge_type, count(*)::int AS sends,
                   round(avg(n.estimated_uplift_pts)::numeric, 2)::float AS avg_uplift_pts,
                   round((sum(n.estimated_uplift_pts) / 100)::numeric, 1)::float AS expected_extra_comebacks,
                   count(*) FILTER (WHERE n.copy_status = 'passed')::int AS copy_passed,
                   count(*) FILTER (WHERE n.copy_status = 'needs_review')::int AS copy_needs_review,
                   min(n.decision_date)::text AS decision_date,
                   c.status AS control_status, c.reviewer AS control_reviewer, c.note AS control_note,
                   c.updated_at AS control_updated_at
            FROM hearth.next_best_action n
            LEFT JOIN console.campaign_controls c ON c.decision_date = n.decision_date AND c.nudge_type = n.nudge_type
            WHERE n.action = 'send'
            GROUP BY n.nudge_type, c.status, c.reviewer, c.note, c.updated_at
            ORDER BY sends DESC`);
          const holds = await db.query(`
            SELECT hold_reason, count(*)::int AS members FROM hearth.next_best_action
            WHERE action = 'hold' GROUP BY hold_reason ORDER BY members DESC`);
          return { by_nudge: byNudge.rows, holds: holds.rows };
        });
        res.json({ ...result, ms });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    app.post('/api/plan/controls', async (req, res) => {
      const parsed = ControlBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'Invalid control', issues: parsed.error.issues });
        return;
      }
      const b = parsed.data;
      try {
        const { rows } = await db.query(
          `INSERT INTO console.campaign_controls (decision_date, nudge_type, status, note, reviewer)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (decision_date, nudge_type)
           DO UPDATE SET status = EXCLUDED.status, note = EXCLUDED.note, reviewer = EXCLUDED.reviewer, updated_at = NOW()
           RETURNING decision_date::text, nudge_type, status, note, reviewer, updated_at`,
          [b.decision_date, b.nudge_type, b.status, b.note ?? null, reviewer(req)],
        );
        res.json(rows[0]);
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    // Distinct values for the segment builder's filters
    app.get('/api/segments/options', async (_req, res) => {
      try {
        const out: Record<string, string[]> = {};
        for (const [key, col] of Object.entries(SEGMENT_FILTERS)) {
          const { rows } = await db.query(
            `SELECT DISTINCT ${col} AS v FROM hearth.member_segments s
             JOIN hearth.next_best_action n USING (member_id) WHERE ${col} IS NOT NULL ORDER BY 1`,
          );
          out[key] = rows.map((r) => String(r.v));
        }
        res.json(out);
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    // Segment counts and the first rows of the queue for any combination of filters, straight from Lakebase
    app.get('/api/segments', async (req, res) => {
      const where: string[] = [];
      const params: unknown[] = [];
      for (const [key, col] of Object.entries(SEGMENT_FILTERS) as [SegmentKey, string][]) {
        const v = req.query[key];
        if (typeof v === 'string' && v !== '' && v !== 'all') {
          params.push(v);
          where.push(`${col} = $${params.length}`);
        }
      }
      const clause = where.length ? `WHERE ${where.join(' AND ')}` : '';
      try {
        const { result, ms } = await timed(async () => {
          const totals = await db.query(
            `SELECT count(*)::int AS members,
                    count(*) FILTER (WHERE n.action = 'send')::int AS sends,
                    round(avg(s.lapse_risk)::numeric, 3)::float AS mean_lapse_risk,
                    round((sum(n.estimated_uplift_pts) FILTER (WHERE n.action = 'send') / 100)::numeric, 1)::float
                      AS expected_extra_comebacks
             FROM hearth.member_segments s JOIN hearth.next_best_action n USING (member_id) ${clause}`,
            params,
          );
          const rows = await db.query(
            `SELECT n.member_id, n.action, n.hold_reason, n.nudge_type, n.send_hour_local::int AS send_hour_local,
                    n.estimated_uplift_pts, n.copy_status, s.age_band, s.market_size, s.plan, s.days_away_bucket,
                    s.risk_band, round(s.lapse_risk::numeric, 3)::float AS lapse_risk
             FROM hearth.member_segments s JOIN hearth.next_best_action n USING (member_id) ${clause}
             ORDER BY (n.action = 'send') DESC, s.lapse_risk DESC NULLS LAST, n.member_id
             LIMIT 50`,
            params,
          );
          return { totals: totals.rows[0], rows: rows.rows };
        });
        res.json({ ...result, ms, filters: where.length });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    // Messages to review: everything that failed the check first, then the rest by estimated uplift
    app.get('/api/copy', async (req, res) => {
      const status = typeof req.query.status === 'string' && req.query.status !== 'all' ? req.query.status : null;
      const nudge = typeof req.query.nudge_type === 'string' && req.query.nudge_type !== 'all' ? req.query.nudge_type : null;
      const limit = Math.min(Number(req.query.limit ?? 40) || 40, 200);
      try {
        const { result, ms } = await timed(() =>
          db.query(
            `SELECT n.member_id, n.decision_date::text AS decision_date, n.nudge_type, n.copy_text, n.copy_status,
                    n.send_at_utc, n.send_hour_local::int AS send_hour_local, n.timezone, n.estimated_uplift_pts,
                    n.risk_band, r.decision AS review_decision, r.final_copy AS reviewed_copy, r.reviewer,
                    r.created_at AS reviewed_at
             FROM hearth.next_best_action n
             LEFT JOIN LATERAL (
               SELECT decision, final_copy, reviewer, created_at FROM console.copy_reviews r
               WHERE r.member_id = n.member_id AND r.decision_date = n.decision_date
               ORDER BY created_at DESC LIMIT 1
             ) r ON true
             WHERE n.action = 'send' AND ($1::text IS NULL OR n.copy_status = $1) AND ($2::text IS NULL OR n.nudge_type = $2)
             ORDER BY (n.copy_status = 'needs_review') DESC, (r.decision IS NULL) DESC, n.estimated_uplift_pts DESC, n.member_id
             LIMIT $3`,
            [status, nudge, limit],
          ),
        );
        const counts = await db.query(`
          SELECT copy_status, count(*)::int AS messages FROM hearth.next_best_action
          WHERE action = 'send' GROUP BY copy_status`);
        res.json({ rows: result.rows, counts: counts.rows, ms });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    app.post('/api/copy/check', async (req, res) => {
      const parsed = CheckBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'Invalid message', issues: parsed.error.issues });
        return;
      }
      try {
        const { result, ms } = await timed(() => checkCopy(parsed.data.member_id, parsed.data.copy));
        if (!result) {
          res.status(404).json({ error: 'No generated copy for this member on the latest decision date' });
          return;
        }
        res.json({ ...result, ms });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    // Approving always re-runs the check on the server, so nothing unchecked can be approved from the browser
    app.post('/api/copy/review', async (req, res) => {
      const parsed = ReviewBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'Invalid review', issues: parsed.error.issues });
        return;
      }
      const b = parsed.data;
      try {
        const { rows: nba } = await db.query(
          `SELECT decision_date::text AS decision_date, nudge_type, copy_text FROM hearth.next_best_action
           WHERE member_id = $1 AND action = 'send'`,
          [b.member_id],
        );
        if (nba.length === 0) {
          res.status(404).json({ error: 'This member has no send planned' });
          return;
        }
        const check = b.decision === 'approved' ? await checkCopy(b.member_id, b.copy) : null;
        if (b.decision === 'approved' && !check?.passed) {
          res.status(422).json({ error: 'The message did not pass the copy check, so it cannot be approved', check });
          return;
        }
        const { rows } = await db.query(
          `INSERT INTO console.copy_reviews
             (member_id, decision_date, nudge_type, original_copy, final_copy, decision, check_passed, check_reasons, reviewer)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           RETURNING review_id::text, member_id, decision_date::text, decision, final_copy, reviewer, created_at`,
          [b.member_id, nba[0].decision_date, nba[0].nudge_type, nba[0].copy_text, b.copy, b.decision,
           check?.passed ?? null, check?.reasons ?? null, reviewer(req)],
        );
        res.status(201).json({ review: rows[0], check });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });

    // One member's next action, by id, the way the notification service reads it
    app.get('/api/members/:id', async (req, res) => {
      const id = req.params.id;
      if (!/^m_\d{6,7}$/.test(id)) {
        res.status(400).json({ error: 'Member ids look like m_000123' });
        return;
      }
      try {
        const { result, ms } = await timed(async () => {
          const { rows } = await db.query(
            `SELECT n.member_id, n.decision_date::text AS decision_date, n.action, n.hold_reason, n.nudge_type,
                    n.send_at_utc, n.send_hour_local::int AS send_hour_local, n.timezone, n.estimated_uplift_pts,
                    n.net_value_pts, n.lapse_risk, n.risk_band, n.copy_text, n.copy_status, n.policy_version,
                    s.age_band, s.market_size, s.platform, s.plan, s.tenure_days::int AS tenure_days, s.days_away,
                    s.days_away_bucket, s.pushes_7d::int AS pushes_7d, s.preferred_hour_local::int AS preferred_hour_local,
                    s.hour_source, s.as_of::text AS segments_as_of,
                    c.status AS control_status, r.decision AS review_decision, r.final_copy AS reviewed_copy,
                    r.reviewer, r.created_at AS reviewed_at
             FROM hearth.next_best_action n
             LEFT JOIN hearth.member_segments s USING (member_id)
             LEFT JOIN console.campaign_controls c ON c.decision_date = n.decision_date AND c.nudge_type = n.nudge_type
             LEFT JOIN LATERAL (
               SELECT decision, final_copy, reviewer, created_at FROM console.copy_reviews r
               WHERE r.member_id = n.member_id AND r.decision_date = n.decision_date ORDER BY created_at DESC LIMIT 1
             ) r ON true
             WHERE n.member_id = $1`,
            [id],
          );
          return rows[0] ?? null;
        });
        if (!result) {
          res.status(404).json({ error: 'Not in tomorrow\'s plan. A new member can be scored live below.', ms });
          return;
        }
        res.json({ member: result, ms });
      } catch (err) {
        res.status(500).json({ error: (err as Error).message });
      }
    });
  });
}
