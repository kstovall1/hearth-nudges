const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : JSON.stringify(v));

export const int = (v: unknown) => (v == null ? '-' : Math.round(Number(v)).toLocaleString('en-US'));
export const dec = (v: unknown, places = 1) =>
  v == null ? '-' : Number(v).toLocaleString('en-US', { minimumFractionDigits: places, maximumFractionDigits: places });
export const pct = (v: unknown, places = 1) => (v == null ? '-' : `${dec(Number(v) * 100, places)}%`);
export const pts = (v: unknown) => (v == null ? '-' : `${Number(v) > 0 ? '+' : ''}${dec(v, 1)} pts`);
export const day = (v: unknown) =>
  v == null ? '-' : new Date(str(v).slice(0, 10) + 'T00:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
export const when = (v: unknown) =>
  v == null ? '-' : new Date(str(v)).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short' });
export const hour = (h: unknown) => {
  if (h == null) return '-';
  const n = Number(h);
  return `${n % 12 === 0 ? 12 : n % 12}${n < 12 ? 'am' : 'pm'}`;
};
export const label = (v: unknown) => (v == null ? '-' : str(v).replace(/_/g, ' '));
