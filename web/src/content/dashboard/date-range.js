export const RANGE_PRESETS = [
  { key: "last1h", label: "最近 1 小时", minutes: 60 },
  { key: "last6h", label: "最近 6 小时", minutes: 360 },
  { key: "last12h", label: "最近 12 小时", minutes: 720 },
  { key: "last24h", label: "最近 24 小时", minutes: 1440 },
  { key: "today", label: "今天" },
  { key: "yesterday", label: "昨天" },
  { key: "last7d", label: "最近 7 天", minutes: 10080 },
  { key: "last30d", label: "最近 30 天", minutes: 43200 },
];

export function localMinute(date, zone) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  const pick = type => parts.find(part => part.type === type)?.value;
  return `${pick("year")}-${pick("month")}-${pick("day")}T${pick("hour")}:${pick("minute")}`;
}

export function presetRange(key, timezone, now = Date.now()) {
  const preset = RANGE_PRESETS.find(item => item.key === key);
  if (!preset) throw new Error("无效的快捷时间范围");
  // End is exclusive; include the current, possibly unfinished minute.
  const endInstant = Math.floor(now / 60000) * 60000 + 60000;
  const end = localMinute(new Date(endInstant), timezone);
  const day = localMinute(new Date(now), timezone).slice(0, 10);
  if (preset.minutes) return { start: localMinute(new Date(endInstant - preset.minutes * 60000), timezone), end, timezone };
  if (key === "today") return { start: day + "T00:00", end, timezone };
  const yesterday = new Date(Date.parse(day + "T12:00:00Z") - 86400000).toISOString().slice(0, 10);
  return { start: yesterday + "T00:00", end: day + "T00:00", timezone };
}

export function validMinute(value) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(value + ":00Z");
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 16) === value;
}

export function rangeError(value) {
  if (!validMinute(value?.start) || !validMinute(value?.end)) return "请填写完整的年月日、小时和分钟";
  if (value.end <= value.start) return "结束时间必须晚于开始时间";
  try { new Intl.DateTimeFormat("en", { timeZone: value.timezone }); } catch { return "请选择有效的统计时区"; }
  return "";
}

export function rememberRange(history, range) {
  const signature = value => JSON.stringify(value.relativeKey
    ? [value.timezone, value.relativeKey]
    : [value.timezone, value.start, value.followNow ? "now" : value.end]);
  return [range, ...history.filter(item => signature(item) !== signature(range))].slice(0, 5);
}

export function restoreHistory(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.filter(item => item && !rangeError(item)
    && (!item.relativeKey || RANGE_PRESETS.some(preset => preset.key === item.relativeKey)))
    .slice(0, 5);
}

export const displayMinute = value => value?.replace("T", " ") || "";
export const zoneLabel = zone => zone === "Asia/Shanghai" ? "北京时间" : zone;

export function moveEndpointDay(range, index, day) {
  if (![0, 1].includes(index) || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const next = [...range];
  next[index] = day + "T" + range[index].slice(11, 16);
  if (!validMinute(next[index]) || next[0] >= next[1]) return null;
  return next;
}
