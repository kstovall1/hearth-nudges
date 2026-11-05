// Runs a governed SQL statement on the app's warehouse as the app's service principal. Used for the copy check,
// which is an action rather than a read: it must run every time, never from the analytics cache.
import { createWorkspaceClient, getWarehouseId } from '@databricks/appkit';

export type SqlParam = { name: string; value: string | null; type?: string };

let client: ReturnType<typeof createWorkspaceClient> | null = null;

export async function runStatement(statement: string, parameters: SqlParam[]): Promise<Record<string, string | null>[]> {
  client ??= createWorkspaceClient();
  const warehouseId = await getWarehouseId();
  let res = await client.statementExecution.executeStatement({
    warehouse_id: warehouseId,
    statement,
    parameters: parameters.map((p) => ({ name: p.name, value: p.value ?? undefined, type: p.type ?? 'STRING' })),
    wait_timeout: '50s',
  });
  // Long statements keep running past the wait; poll until they finish
  for (let i = 0; i < 60 && (res.status?.state === 'PENDING' || res.status?.state === 'RUNNING'); i++) {
    await new Promise((r) => setTimeout(r, 1000));
    res = await client.statementExecution.getStatement({ statement_id: res.statement_id ?? '' });
  }
  if (res.status?.state !== 'SUCCEEDED') {
    throw new Error(res.status?.error?.message ?? `Statement ${res.status?.state ?? 'failed'}`);
  }
  const cols = res.manifest?.schema?.columns?.map((c) => c.name ?? '') ?? [];
  return (res.result?.data_array ?? []).map((row) => Object.fromEntries(cols.map((c, i) => [c, row[i] ?? null])));
}
