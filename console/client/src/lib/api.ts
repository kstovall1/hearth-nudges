// Small fetch helpers for the console's own API routes (server/console/routes.ts).
import { useCallback, useEffect, useState } from 'react';

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function parse<T>(res: Response): Promise<T> {
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = body && typeof body === 'object' && 'error' in body ? String((body as { error: unknown }).error) : res.statusText;
    throw new ApiError(msg, res.status, body);
  }
  return body as T;
}

export async function getJson<T>(url: string): Promise<T> {
  return parse<T>(await fetch(url));
}

export async function postJson<T>(url: string, payload: unknown): Promise<T> {
  return parse<T>(await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }));
}

/** Fetch a GET route and keep its loading, error and data state. reload() fetches it again, keeping the last data. */
export function useApi<T>(url: string | null) {
  const [tick, setTick] = useState(0);
  const key = url === null ? null : `${url}#${tick}`;
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({ key: null, data: null, error: null });
  useEffect(() => {
    if (url === null) return;
    let live = true;
    getJson<T>(url)
      .then((data) => live && setState({ key, data, error: null }))
      .catch((e: Error) => live && setState((s) => ({ key, data: s.data, error: e.message })));
    return () => {
      live = false;
    };
  }, [url, key]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, error: state.key === key ? state.error : null, loading: key !== null && state.key !== key, reload };
}
