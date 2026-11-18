// Server values are UTC ISO 8601 ("...Z"). People read KST (Asia/Seoul).
const TZ = "Asia/Seoul";

function parts(iso: string): Record<string, string> {
  const d = new Date(iso);
  const out: Record<string, string> = {};
  for (const p of new Intl.DateTimeFormat("en-CA", {
    timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(d)) out[p.type] = p.value;
  return out;
}

/** "2026-11-17 13:05 KST" */
export function kstFull(iso: string): string {
  const p = parts(iso);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} KST`;
}

/** "13:05" */
export function kstTime(iso: string): string {
  const p = parts(iso);
  return `${p.hour}:${p.minute}`;
}

/** Short label for lists: time today (KST), else month/day. */
export function kstShort(iso: string, now: Date = new Date()): string {
  const p = parts(iso);
  const n = parts(now.toISOString());
  if (p.year === n.year && p.month === n.month && p.day === n.day) return `${p.hour}:${p.minute}`;
  if (p.year === n.year) return `${p.month}/${p.day}`;
  return `${p.year}-${p.month}-${p.day}`;
}

/** "11/17 (화)" for day separators */
export function kstDay(iso: string): string {
  const d = new Date(iso);
  const p = parts(iso);
  const wd = new Intl.DateTimeFormat("ko-KR", { timeZone: TZ, weekday: "short" }).format(d);
  return `${p.month}/${p.day} (${wd})`;
}
