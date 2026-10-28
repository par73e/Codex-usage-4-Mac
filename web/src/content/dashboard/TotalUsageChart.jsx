import React, { useMemo, useState } from "react";
import { barChartSpec, DataComponent, DataTable, EvidenceChart } from "../../data-app-public.jsx";
import { totalBreakdown, orderedTooltipSeries, cacheHitRate, cacheHitTooltipFields } from "./token-series.js";

const number = n => new Intl.NumberFormat("zh-CN").format(n || 0);
const compact = n => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n || 0);

const timeFormats = new Map();
function formatTime(value, timezone, precise = false) {
  const key = timezone + (precise ? ":precise" : "");
  if (!timeFormats.has(key)) {
    if (timeFormats.size >= 12) timeFormats.delete(timeFormats.keys().next().value);
    timeFormats.set(key, new Intl.DateTimeFormat("zh-CN", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", ...(precise ? { second: "2-digit", fractionalSecondDigits: 3 } : {}), hourCycle: "h23" }));
  }
  return timeFormats.get(key).format(new Date(value));
}
function LegacyTable({ result }) {
  const rows = useMemo(() => result.legacy_intervals.map(row => ({
    interval_id: row.interval_id, session: row.title,
    period: `${row.start_unknown ? "起点未知" : formatTime(row.start, result.timezone, true)} ～ ${formatTime(row.end, result.timezone, true)}`,
    observed_tokens: row.total_tokens, selected_tokens: row.selected_tokens,
    note: [row.counter_reset ? "累计重置后的区间估算" : row.start_unknown ? "起点未知，按观测时段归入" : "区间估算，非精确时间点", ...(row.coarse_timestamp ? ["同一时间多读数，取最大值"] : [])].join(" · "),
  })), [result.legacy_intervals, result.timezone]);
  return <DataComponent id="legacy-interval-table" queryId="legacy_usage" title="旧日志观测区间" kind="table" displayRows={rows} sourceRows={result.legacyEvidenceRows}
    description="覆盖顶部所选时间与会话，不随图表缩放裁剪；区间总量来自累计快照差值，所选范围部分按时长分摊。">
    <DataTable rows={rows} columns={[{ field: "session", label: "会话" }, { field: "period", label: "原始观测区间" }, { field: "observed_tokens", label: "原区间 Token" }, { field: "selected_tokens", label: "所选范围估算 Token" }, { field: "note", label: "精度说明" }]}
      caption="旧日志用量观测区间，覆盖所选日期及会话" rowKey="interval_id" />
  </DataComponent>;
}

export const TotalUsageChart = React.memo(function TotalUsageChart({ result, mode }) {
  const [tableOpen, setTableOpen] = useState(false), [legacyOpen, setLegacyOpen] = useState(false);
  const { efforts, buckets, series } = useMemo(() => totalBreakdown(result, mode), [result, mode]);
  const rows = useMemo(() => buckets.map((bucket, index) => {
    const bin = result.bins[index];
    const row = { period: bin.label, range: `${formatTime(bin.start, result.timezone)} ～ ${formatTime(bin.end, result.timezone)}`,
      total: bucket.tokens || null, recorded: bucket.tokens ? bucket.recorded : null, estimated: bucket.tokens ? bucket.estimated : null };
    for (const s of series) row[s.key] = bucket.tokens ? s.key === "estimated" ? bucket.estimated : mode === "effort" ? bucket.efforts[s.key] || 0 : bucket[s.key] || 0 : null;
    if (mode === "composition") row.cache_hit_rate = cacheHitRate(bucket.cached, bucket.tokens);
    return row;
  }), [buckets, series, mode, result.bins, result.timezone]);
  const sourceRowsByQuery = useMemo(() => {
    const ids = new Set(result.models.flatMap(model => model.response_ids));
    const intervals = new Set(result.legacy_intervals.map(row => row.interval_id));
    return { usage: result.evidenceRows.filter(row => ids.has(row.response_id)), legacy_usage: result.legacyEvidenceRows.filter(row => intervals.has(row.interval_id)) };
  }, [result.models, result.evidenceRows, result.legacy_intervals, result.legacyEvidenceRows]);
  const spec = barChartSpec({ presentation: "plot", category: "period", categoryTickLayout: result.bins.some(bin => bin.label.includes(":")) ? "time-date" : "default",
    tooltipLabelField: "range", value: "total", valueWidth: 72, domain: [0, Math.max(1, ...result.total_buckets.map(bucket => bucket.tokens)) * 1.08], grid: true,
    barCategoryGap: "25%", style: { thickness: 25, radius: 3 }, series: series.map(s => ({ ...s, stackId: "total" })), labels: { value: false }, format: { maximumFractionDigits: 0 },
    tooltipFields: [{ key: "total", label: "总用量 Token" }, ...cacheHitTooltipFields(mode), ...orderedTooltipSeries(mode, series)] });
  return <section className="usage-total-section" aria-label="所有模型合计用量">
    <EvidenceChart id="all-models-usage" queryId="usage" queryIds={["usage", "legacy_usage"]} sourceRowsByQuery={sourceRowsByQuery}
      title="所有模型合计用量" rows={rows} spec={spec} variant="card" height={285}
      description="跟随顶部时间、会话和图表缩放。逐响应明细按记录时间统计；旧日志的累计差值保留观测区间，按时长分摊到时间桶，表示估算而非精确时间点。"
      scopeFilters={[{ label: "时间范围", value: `${result.start} 至 ${result.end}` }, { label: "时区", value: result.timezone }, { label: "范围", value: "所有模型 · 当前所选会话" }]}
      headerControls={<div className="usage-model-total"><strong title={number(result.totals.total_tokens)}>{compact(result.totals.total_tokens)}</strong><span>tokens</span></div>}
      renderPlot={plot => <><div className="usage-model-legend">{series.map(s => <span key={s.key}><i style={{ background: s.color }} /><span>{s.label}</span>{mode === "effort" && <strong>{(((s.key === "estimated" ? result.totals.estimated_tokens : efforts[s.key]) || 0) / (result.totals.total_tokens || 1) * 100).toFixed(1)}%</strong>}</span>)}</div><div className="usage-chart-plot">{plot}</div></>}>
      <div className="usage-model-caption" data-reviewed-rows><span>所选范围：明细 {number(result.totals.recorded_tokens)} · 区间估算 {number(result.totals.estimated_tokens)} Token</span><span>旧累计不计入响应次数</span></div>
      {result.legacy_intervals.length > 0 && <p className="usage-estimate-note">旧日志用量表示两个观测之间的累计增量，按时长分摊到各柱；不表示实际均匀消耗。模型、推理强度与压缩类型未可靠记录的部分不作推断。</p>}
      <details className="usage-total-detail" onToggle={event => setTableOpen(event.currentTarget.open)}><summary>查看合计用量表</summary>{tableOpen && <DataTable rows={rows.map(row => ({ period: row.range, ...Object.fromEntries(series.map(s => [s.key, row[s.key] || 0])), total: row.total || 0 }))}
        columns={[{ field: "period", label: "统计时段" }, ...series.map(s => ({ field: s.key, label: s.label + " Token" })), { field: "total", label: "合计 Token" }]} caption="所有模型合计用量，与当前图表显示范围一致" />}</details>
    </EvidenceChart>
    {result.legacy_intervals.length > 0 && <details className="usage-total-detail usage-legacy-detail" onToggle={event => setLegacyOpen(event.currentTarget.open)}><summary>查看旧日志观测区间 · {number(result.legacy_intervals.length)} 条</summary>
      {legacyOpen && <LegacyTable result={{ ...result, legacyEvidenceRows: sourceRowsByQuery.legacy_usage }} />}</details>}
  </section>;
});
