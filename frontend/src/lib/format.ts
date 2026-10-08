export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function shortId(id: string | null | undefined, keep = 6): string {
  if (!id) return "-";
  return id.length <= keep * 2 + 3 ? id : `${id.slice(0, keep)}…${id.slice(-keep)}`;
}

export function shortDigest(d: string | null | undefined): string {
  if (!d) return "-";
  const [, hex] = d.split(":");
  return hex ? `sha256:${hex.slice(0, 12)}…` : d;
}

/** Last path segment of an ARM/ARN-style identifier, for compact display. */
export function leaf(id: string | null | undefined): string {
  if (!id) return "-";
  const parts = id.split(/[/:]/).filter(Boolean);
  return parts[parts.length - 1] ?? id;
}

export function humanize(s: string | null | undefined): string {
  if (!s) return "-";
  return s
    .toLowerCase()
    .split("_")
    .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export function ratioText(r: { value: string; percent: number | null } | null | undefined): string {
  if (!r) return "N/A";
  return r.percent === null ? "N/A" : `${r.value} (${r.percent}%)`;
}
