export const EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "unknown"];
export const E_LABEL = { none: "none", minimal: "minimal", low: "low", medium: "medium", high: "high", xhigh: "xhigh", max: "max", ultra: "ultra", unknown: "未知强度" };
export const E_COLORS = { none: "var(--effort-none)", minimal: "var(--effort-minimal)", low: "var(--effort-low)", medium: "var(--effort-medium)", high: "var(--effort-high)", xhigh: "var(--effort-xhigh)", max: "var(--effort-max)", ultra: "var(--effort-ultra)", unknown: "var(--chart-8)" };
export const C_SERIES = [{ key: "cached", label: "输入 · 命中缓存", color: "var(--token-cached)" }, { key: "uncached", label: "输入 · 未命中缓存", color: "var(--token-uncached)" }, { key: "output", label: "输出", color: "var(--token-output)" }];

export function cacheHitRate(cached, total) {
  return total > 0 ? (cached || 0) / total : null;
}

export function cacheHitTooltipFields(mode) {
  return mode === "composition" ? [{ key: "cache_hit_rate", label: "缓存命中率", format: "percent", color: "var(--secondary)" }] : [];
}

export function tokenSeries(mode, efforts, buckets) {
  return mode === "effort"
    ? [...new Set([...EFFORTS, ...Object.keys(efforts)])].filter(key => efforts[key] > 0)
      .map(key => ({ key, label: E_LABEL[key] || key, color: E_COLORS[key] || "var(--chart-8)" }))
    : [...C_SERIES, ...(buckets.some(bucket => bucket.cache_unknown) ? [{ key: "cache_unknown", label: "输入 · 缓存状态未知", color: "var(--chart-8)" }] : [])];
}

export function orderedTooltipSeries(mode, series) {
  return mode === "effort" ? ["medium", "high", ...series.map(s => s.key).filter(key => !["medium", "high"].includes(key))]
    .map(key => series.find(s => s.key === key)).filter(Boolean)
    .map(s => ({ ...s, label: s.key === "medium" ? "Medium" : s.key === "high" ? "High" : s.label })) : series;
}

export function totalBreakdown(result, mode) {
  const efforts = {};
  for (const model of result.models) for (const [key, tokens] of Object.entries(model.efforts)) efforts[key] = (efforts[key] || 0) + tokens;
  const buckets = result.total_buckets.map((bucket, index) => {
    const merged = { ...bucket, efforts: {}, cached: 0, uncached: 0, output: 0, cache_unknown: 0 };
    for (const model of result.models) {
      const source = model.buckets[index];
      for (const field of ["cached", "uncached", "output", "cache_unknown"]) merged[field] += source[field] || 0;
      for (const [key, tokens] of Object.entries(source.efforts)) merged.efforts[key] = (merged.efforts[key] || 0) + tokens;
    }
    return merged;
  });
  const series = tokenSeries(mode, efforts, buckets);
  if (result.totals.estimated_tokens > 0) series.push({ key: "estimated", label: "旧日志 · 区间估算", color: "var(--chart-8)" });
  return { efforts, buckets, series };
}
