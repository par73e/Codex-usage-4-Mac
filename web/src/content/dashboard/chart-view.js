// Limit drawn categories, not evidence or query precision. The existing zoom
// slider keeps original bucket indices and restores exact buckets when close.
export const MAX_PLOT_BUCKETS = 120;
const STRIDES = [1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 168, 336, 720];
const VALUES = ["tokens", "responses", "cached", "uncached", "cache_unknown", "output"];

export function buildChartView(result, zoom, limit = MAX_PLOT_BUCKETS) {
  if (!result) return null;
  const start = Math.max(0, Math.min(result.bins.length - 1, zoom[0]));
  const end = Math.max(start, Math.min(result.bins.length - 1, zoom[1]));
  const count = end - start + 1;
  const needed = Math.max(1, Math.ceil(count / Math.max(1, limit)));
  const stride = STRIDES.find(value => value >= needed) || needed;
  const groups = [];
  for (let from = start; from <= end; from += stride) groups.push([from, Math.min(end + 1, from + stride)]);
  const bins = groups.map(([from, to]) => {
    const first = result.bins[from], last = result.bins[to - 1];
    return stride === 1 ? first : { ...first, id: `${first.id}/${last.id}`, end: last.end, end_ms: last.end_ms,
      partial: first.partial || last.partial, bucket_count: to - from };
  });
  const models = result.models.map(model => ({ ...model, buckets: groups.map(([from, to], index) => {
    if (stride === 1) return model.buckets[from];
    const bucket = { id: bins[index].id, efforts: {}, ...Object.fromEntries(VALUES.map(field => [field, 0])) };
    for (let i = from; i < to; i++) {
      const source = model.buckets[i];
      for (const field of VALUES) bucket[field] += source[field] || 0;
      for (const [effort, tokens] of Object.entries(source.efforts)) bucket.efforts[effort] = (bucket.efforts[effort] || 0) + tokens;
    }
    return bucket;
  }) }));
  const total_buckets = result.total_buckets?.length ? groups.map(([from, to], index) => {
    const bucket = { id: bins[index].id, tokens: 0, recorded: 0, estimated: 0, responses: 0 };
    for (let i = from; i < to; i++) for (const field of ["tokens", "recorded", "estimated", "responses"]) bucket[field] += result.total_buckets[i][field] || 0;
    return bucket;
  }) : [];
  return { ...result, bins, models, total_buckets, overviewStride: stride, visibleBucketCount: count };
}
