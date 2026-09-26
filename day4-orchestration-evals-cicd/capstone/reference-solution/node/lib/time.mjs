// Instants with their offset. JavaScript's Date forgets the offset, and the SLA clock is always
// read and printed in the offset it was given (the seed data is IST, +05:30), so we keep both.
//
// One timestamp format in every language (SPEC): 2026-09-24T10:30:00+05:30 - seconds always present,
// the offset always written as +hh:mm (never "Z").

const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

export class Instant {
  constructor(ms, offsetMinutes) { this.ms = ms; this.offset = offsetMinutes; }

  isAfter(other) { return this.ms > other.ms; }

  toString() {
    const d = new Date(this.ms + this.offset * 60000);
    const p = (n, w = 2) => String(n).padStart(w, "0");
    const o = Math.abs(this.offset);
    return `${p(d.getUTCFullYear(), 4)}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
      + `:${p(d.getUTCSeconds())}${this.offset < 0 ? "-" : "+"}${p(Math.floor(o / 60))}:${p(o % 60)}`;
  }

  toJSON() { return this.toString(); }

  static now(offsetMinutes = 330) { return new Instant(Date.now(), offsetMinutes); }
}

export class InvalidInstant extends Error {}

/** ISO-8601 with an offset, e.g. 2026-09-24T10:30:00+05:30. Anything else is refused. */
export function parseInstant(s) {
  const m = typeof s === "string" ? ISO.exec(s) : null;
  if (!m) throw new InvalidInstant(`as_of must be ISO-8601 with an offset, e.g. 2026-09-24T10:30:00+05:30 (got ${s ?? "null"})`);
  const [, y, mo, d, h, mi, sec = "0", frac = "", off] = m;
  const offset = off === "Z" ? 0 : (off[0] === "-" ? -1 : 1) * (Number(off.slice(1, 3)) * 60 + Number(off.slice(4, 6)));
  const ms = Date.UTC(+y, +mo - 1, +d, +h, +mi, +sec, frac ? Math.floor(Number(frac) * 1000) : 0) - offset * 60000;
  if (Number.isNaN(ms)) throw new InvalidInstant(`as_of must be ISO-8601 with an offset, e.g. 2026-09-24T10:30:00+05:30 (got ${s})`);
  return new Instant(ms, offset);
}

/** Whole minutes from a to b, truncated toward zero (Duration.toMinutes). */
export function minutesBetween(a, b) { return Math.trunc((b.ms - a.ms) / 60000); }

/** Now in IST as yyyy-MM-ddTHH:mm:ss+05:30 - the store's created_at / decided-at format. */
export function nowIst() {
  const d = new Date(Date.now() + 330 * 60000);
  return d.toISOString().slice(0, 19) + "+05:30";
}
