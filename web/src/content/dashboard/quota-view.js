export const QUOTA_LEFT = 72, QUOTA_RIGHT = 8;
// Log snapshot IDs are stable across fetches. Preserve unchanged evidence rows
// so shared hover marks do not mistake a refresh for a different observation.
export function retainQuotaRows(previous, incoming, key) {
  const oldRows = new Map(previous.map(row => [row[key], row]));
  const next = incoming.map(row => {
    const old = oldRows.get(row[key]);
    return old && Object.keys(old).length === Object.keys(row).length
      && Object.keys(row).every(field => Object.is(old[field], row[field])) ? old : row;
  });
  return next.length === previous.length && next.every((row, index) => row === previous[index]) ? previous : next;
}
export function windowLabel(minutes) {
  if (minutes % 1440 === 0) return `${minutes / 1440} 天窗口`;
  if (minutes % 60 === 0) return `${minutes / 60} 小时窗口`;
  return `${minutes} 分钟窗口`;
}
export function quotaView(window, bins, zoom) {
  const visibleBins = bins.slice(zoom[0], zoom[1] + 1);
  if (!visibleBins.length) return { bins: [], points: [], context: null, events: [], start: 0, end: 1 };
  const start = visibleBins[0].start_ms, end = visibleBins.at(-1).end_ms;
  const history = [window.context_before, ...window.points].filter(Boolean);
  return { bins: visibleBins, start, end,
    points: history.filter(p => p.timestamp_ms >= start && p.timestamp_ms < end),
    context: history.filter(p => p.timestamp_ms < start).at(-1) || null,
    events: window.events.filter(e => e.upper_ms >= start && e.upper_ms < end) };
}
// Token bars have equal-width bins, including a clipped first/last bucket. Map
// each exact snapshot into its matching bin so both plots share those edges.
export function quotaPosition(timestamp, bins) {
  if (!bins.length) return 0;
  let low = 0, high = bins.length - 1;
  while (low < high) { const middle = Math.ceil((low + high) / 2); if (bins[middle].start_ms <= timestamp) low = middle; else high = middle - 1; }
  const bin = bins[low];
  return (low + (timestamp - bin.start_ms) / Math.max(1, bin.end_ms - bin.start_ms)) / bins.length;
}
export function quotaEventBand(event, bins, width) {
  const start = Math.max(0, Math.min(width, quotaPosition(event.lower_ms, bins) * width));
  const end = Math.max(0, Math.min(width, quotaPosition(event.upper_ms, bins) * width));
  const actualWidth = Math.max(0, end - start), cueWidth = Math.min(8, width);
  return { start, end, actualWidth, narrow: actualWidth < cueWidth,
    cueStart: Math.max(0, Math.min(width - cueWidth, end - cueWidth / 2)), cueWidth };
}
// Keep the reviewed evidence. Only thin overlapping plotted hover
// buttons, retaining extrema and the endpoints of every observed recovery.
export function quotaMarks(points, bins, width, events) {
  const mandatory = new Set(events.flatMap(e => [e.lower_ms, e.upper_ms]));
  const cells = new Map(), keep = new Map();
  points.forEach((point, index) => {
    const cell = Math.floor(quotaPosition(point.timestamp_ms, bins) * width / 12);
    const group = cells.get(cell) || []; group.push(point); cells.set(cell, group);
    if (index === 0 || index === points.length - 1 || mandatory.has(point.timestamp_ms)) keep.set(point.snapshot_id, point);
  });
  for (const group of cells.values()) {
    const low = group.reduce((a, b) => a.remaining_percent < b.remaining_percent ? a : b);
    const high = group.reduce((a, b) => a.remaining_percent > b.remaining_percent ? a : b);
    for (const point of [group[0], group.at(-1), low, high]) keep.set(point.snapshot_id, point);
  }
  return [...keep.values()].sort((a, b) => a.timestamp_ms - b.timestamp_ms);
}
