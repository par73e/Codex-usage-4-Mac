import React, { useEffect, useMemo, useRef, useState } from "react";
import { barChartSpec, Button, DataComponent, EvidenceChart, RangeSlider, SegmentedControl, SortableItem, SortableRegion, Switch, useDataApp } from "../../data-app-public.jsx";
import { TimeRangePicker } from "./TimeRangePicker.jsx";
import { QuotaCharts } from "./QuotaCharts.jsx";
import { TotalUsageChart } from "./TotalUsageChart.jsx";
import { tokenSeries, orderedTooltipSeries, cacheHitRate, cacheHitTooltipFields } from "./token-series.js";
import { buildChartView } from "./chart-view.js";
import { localMinute, presetRange, RANGE_PRESETS } from "./date-range.js";
import "./dashboard.css";

const number = n => new Intl.NumberFormat("zh-CN").format(n ?? 0);
const compact = n => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(n ?? 0);
function preference(key, fallback) { try { return localStorage.getItem("codex-usage:" + key) || fallback; } catch { return fallback; } }
function rangeLabel(bin) {
  const a = bin.start.slice(5, 16).replace("T", " "), b = bin.end.slice(5, 16).replace("T", " ");
  return `${a} – ${b}${bin.partial ? " · 部分时段" : ""}`;
}
async function post(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || "查询失败，请重试。");
  return value;
}

function SessionPicker({ options, selection, onChange, disabled }) {
  const [open, setOpen] = useState(false), [search, setSearch] = useState("");
  const root = useRef(null), trigger = useRef(null);
  useEffect(() => {
    if (!open) return;
    const click = e => { if (!root.current?.contains(e.target)) setOpen(false); };
    const key = e => { if (e.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", click); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", click); document.removeEventListener("keydown", key); };
  }, [open]);
  const ids = selection.mode === "all" ? new Set(options.map(o => o.id)) : new Set(selection.ids);
  const toggle = id => { const next = new Set(ids); next.has(id) ? next.delete(id) : next.add(id); onChange({ mode: "explicit", ids: [...next] }); };
  const shown = options.filter(o => (o.title + o.id).toLowerCase().includes(search.toLowerCase()));
  return <div className="usage-session-picker" ref={root}>
    <button className="usage-session-trigger" ref={trigger} type="button" disabled={disabled} aria-expanded={open} aria-controls="usage-session-options" onClick={() => setOpen(!open)}>
      <span>会话</span><strong>{selection.mode === "all" ? "全部会话" : ids.size ? `已选 ${ids.size} 个会话` : "请选择会话"}</strong><span aria-hidden="true">⌄</span>
    </button>
    {open && <div className="usage-session-popover" id="usage-session-options" role="dialog" aria-label="选择当前时间内的会话">
      <div className="usage-session-search"><input autoFocus placeholder="搜索会话名称或 ID" aria-label="搜索会话" value={search} onChange={e => setSearch(e.target.value)} /></div>
      <div className="usage-session-actions"><button disabled={disabled} onClick={() => onChange({ mode: "all", ids: [] })}>全选当前时间候选</button><button disabled={disabled} onClick={() => onChange({ mode: "explicit", ids: [] })}>清空</button><span>{ids.size} / {options.length}</span></div>
      <div className="usage-session-list">
        {shown.length ? shown.map(o => <label className="usage-session-row" key={o.id}>
          <input type="checkbox" disabled={disabled} checked={ids.has(o.id)} onChange={() => toggle(o.id)} />
          <span className="usage-session-name"><strong title={o.title}>{o.title}</strong><small>{o.kind}{o.estimated_tokens ? " · 含区间估算" : ""} · {o.id.slice(-8)}</small></span><span className="usage-session-tokens">{compact(o.tokens)}<small>tokens</small></span>
        </label>) : <p className="usage-picker-empty">{options.length ? "没有符合搜索的会话" : "当前时间内没有用量记录"}</p>}
      </div>
      <div className="usage-picker-footer">只列出所选时间内有 Token 消耗的会话</div>
    </div>}
  </div>;
}

const ModelChart = React.memo(function ModelChart({ model, result, mode, axis, rawRows, ...sortableProps }) {
  const series = tokenSeries(mode, model.efforts, model.buckets);
  const labelCounts = new Map();
  for (const bin of result.bins) labelCounts.set(bin.label, (labelCounts.get(bin.label) || 0) + 1);
  const rows = model.buckets.map((b, index) => {
    const bin = result.bins[index];
    const repeated = labelCounts.get(bin.label) > 1;
    const data = { period: bin.label + (repeated ? ` ${bin.offset}` : ""), range: rangeLabel(bin), start: bin.start, end: bin.end, total: b.responses ? b.tokens : null, responses: b.responses || null };
    for (const s of series) data[s.key] = b.responses ? mode === "effort" ? b.efforts[s.key] || 0 : b[s.key] || 0 : null;
    if (mode === "composition") data.cache_hit_rate = cacheHitRate(b.cached, b.tokens);
    return data;
  });
  const maximum = axis === "unified" ? Math.max(1, ...result.models.flatMap(m => m.buckets.map(b => b.tokens))) : Math.max(1, ...model.buckets.map(b => b.tokens));
  const tooltipSeries = orderedTooltipSeries(mode, series);
  const spec = barChartSpec({ presentation: "plot", category: "period", categoryTickLayout: result.bins.some(bin => bin.label.includes(":")) ? "time-date" : "default", tooltipLabelField: result.overviewStride > 1 ? "range" : undefined, value: "total", valueWidth: 72, domain: [0, maximum * 1.08], grid: true, barCategoryGap: "25%", style: { thickness: 25, radius: 3 }, series: series.map(s => ({ ...s, stackId: "tokens" })), labels: { value: false }, format: { maximumFractionDigits: 0 }, tooltipFields: [{ key: "total", label: "总用量 Token", color: "var(--secondary)" }, ...cacheHitTooltipFields(mode), ...tooltipSeries, { key: "responses", label: "响应次数" }] });
  const ids = new Set(model.response_ids), sourceRows = rawRows.filter(r => ids.has(r.response_id));
  const id = "model-" + model.name.replace(/[^a-zA-Z0-9_-]/g, "-");
  return <SortableItem {...sortableProps} id={id} label={model.name} kind="chart" className="usage-model-item">
    <EvidenceChart id={id} queryId="usage" title={model.name === "unknown" ? "未知模型" : model.name} rows={rows} sourceRows={sourceRows} spec={spec} variant="card" height={285} className="usage-model-card"
      description="整次响应按日志时间归属；模型与强度为日志配置。上方占比和合计覆盖整个查询区间。空缺表示当前日志未发现记录。"
      scopeFilters={[{ label: "时间范围", value: `${result.start} 至 ${result.end}` }, { label: "时区", value: result.timezone }, { label: "模型", value: model.name }]}
      headerControls={<div className="usage-model-total"><strong title={number(model.tokens)}>{compact(model.tokens)}</strong><span>tokens</span></div>}
      renderPlot={plot => <>
        <div className="usage-model-legend">{series.map(s => <span key={s.key}><i style={{ background: s.color }} /><span>{s.label}</span>{mode === "effort" && <strong title={`${number(model.efforts[s.key])} tokens`}>{(model.efforts[s.key] / model.tokens * 100).toFixed(1)}%</strong>}</span>)}</div>
        <div className="usage-chart-plot">{plot}</div>
      </>}
    >
      <div className="usage-model-caption"><span>{number(model.responses)} 次响应 · {axis === "unified" ? "统一纵轴" : "独立纵轴"}</span><span>{mode === "effort" ? "占比按整个区间的 Token 计算" : "缓存输入和推理输出均不重复计数"}</span></div>
    </EvidenceChart>
  </SortableItem>;
});

export function DashboardContent() {
  const { snapshot, queries, setDashboardBusy, localRefresh } = useDataApp();
  const index = useMemo(() => JSON.parse(queries.index_state.rows[0].status_json), [queries.index_state]);
  const rawRows = queries.usage.rows;
  const [resume] = useState(() => { try { return JSON.parse(localStorage.getItem("codex-usage:view") || "null"); } catch { return null; } });
  const initialZone = preference("timezone", "Asia/Shanghai");
  const [applied, setApplied] = useState(() => resume?.applied || presetRange("last7d", initialZone));
  const [granularity, setGranularity] = useState(() => preference("granularity", "6h"));
  const [mode, setMode] = useState(() => preference("mode", "effort"));
  const [axis, setAxis] = useState(() => preference("axis", "unified"));
  const [includeCompaction, setIncludeCompaction] = useState(resume?.includeCompaction ?? true);
  const [selection, setSelection] = useState(() => resume?.selection || ({ mode: "all", ids: [] }));
  const [followNow, setFollowNow] = useState(resume?.followNow ?? true);
  const [relativeKey, setRelativeKey] = useState(() => resume?.applied
    ? RANGE_PRESETS.some(p => p.key === resume.relativeKey) ? resume.relativeKey : null
    : "last7d");
  const [result, setResult] = useState(null), [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [notice, setNotice] = useState(""), [zoom, setZoom] = useState([0, 0]);
  const plotResult = useMemo(() => buildChartView(result, zoom), [result, zoom]);
  const plotZoom = useMemo(() => [0, Math.max(0, (plotResult?.bins.length || 1) - 1)], [plotResult]);
  const request = useRef(0), previousQuery = useRef(null), previousBins = useRef(0);
  const reconcile = localRefresh?.reconcile;
  const queryKey = JSON.stringify({ applied: { ...applied, start: followNow && relativeKey ? relativeKey : applied.start, end: followNow ? "now" : applied.end }, granularity, includeCompaction, selection });
  useEffect(() => { try { localStorage.setItem("codex-usage:view", JSON.stringify({ applied, selection, includeCompaction, followNow, relativeKey })); } catch {} }, [applied, selection, includeCompaction, followNow, relativeKey]);
  useEffect(() => {
    if (!followNow) return;
    const next = relativeKey ? presetRange(relativeKey, applied.timezone) : { ...applied, end: localMinute(new Date(Date.now() + 60000), applied.timezone) };
    if (applied.start === next.start && applied.end === next.end) return;
    setApplied(next);
  }, [followNow, relativeKey, localRefresh?.checkedAt, applied.timezone, applied.start, applied.end]);
  useEffect(() => { try { for (const [k,v] of Object.entries({ timezone: applied.timezone, granularity, mode, axis })) localStorage.setItem("codex-usage:" + k, v); } catch {} }, [applied.timezone, granularity, mode, axis]);
  useEffect(() => {
    const current = ++request.current; setLoading(true); setError(""); setDashboardBusy(true);
    post("/api/v1/query", { generation: index.generation, start: applied.start, end: applied.end, timezone: applied.timezone, granularity, include_compaction: includeCompaction, sessions: selection }).then(data => {
      if (request.current !== current) return;
      const last = Math.max(0, data.bins.length - 1);
      const changedQuery = previousQuery.current !== queryKey, oldCount = previousBins.current;
      setZoom(current => changedQuery || current[0] === 0 && current[1] === oldCount - 1
        ? [0, last] : [Math.min(current[0], last), Math.min(current[1], last)]);
      previousQuery.current = queryKey; previousBins.current = data.bins.length;
      setResult({ ...data, queryKey, evidenceRows: rawRows, quotaEvidenceRows: queries.quota?.rows || [], legacyEvidenceRows: queries.legacy_usage?.rows || [] });
      if (data.removed_ids.length) { setNotice(`${data.removed_ids.length} 个会话在这个范围内无用量，已移出选择。`); setSelection(s => ({ ...s, ids: data.selected_ids })); }
    }).catch(e => { if (request.current === current) { if (e.message.includes("重新载入") && reconcile) void reconcile(); else setError(e.message); } }).finally(() => { if (request.current === current) { setLoading(false); setDashboardBusy(false); } });
    return () => { request.current++; setDashboardBusy(false); };
  }, [applied, granularity, includeCompaction, selection, index.generation, reconcile, followNow, relativeKey]);
  const total = result?.totals?.total_tokens || 0;
  const snapshotTime = index.latest ? new Date(index.latest).toLocaleString("zh-CN", { timeZone: applied.timezone, hour12: false }) : "暂无记录";
  const checkTime = localRefresh?.checkedAt ? new Date(localRefresh.checkedAt).toLocaleTimeString("zh-CN", { timeZone: applied.timezone, hour12: false }) : "正在检查";
  const coverageDate = value => value ? new Date(value).toLocaleDateString("zh-CN", { timeZone: applied.timezone }) : "暂无记录";
  const includesLegacyHistory = index.legacy_only_files > 0 && result
    && (!index.first || Date.parse(result.start) < Date.parse(index.first))
    && Date.parse(result.end) > Date.parse(index.legacy_first) && Date.parse(result.start) <= Date.parse(index.legacy_latest);
  const applyTimeRange = (value, nextFollow, nextRelative) => {
    setApplied({ start: value.start, end: value.end, timezone: value.timezone });
    setFollowNow(nextFollow); setRelativeKey(nextFollow ? nextRelative : null); setNotice("");
  };
  const toggleFollow = value => {
    setFollowNow(value); setRelativeKey(null);
    if (value) setApplied(a => ({ ...a, end: localMinute(new Date(Date.now() + 60000), a.timezone) }));
  };
  return <article className="usage-workspace">
    <section className="usage-topline"><div><h1>Token 用量</h1><p>按时间、会话和模型查看本地使用记录</p></div><div className="usage-sync"><span role="status" className={localRefresh?.error ? "usage-sync-error" : ""}>{localRefresh?.error ? "同步中断，请手动重试" : localRefresh?.connected ? "手动同步 · 点击刷新读取最新日志" : "正在连接本机服务…"}</span><Button onClick={() => void localRefresh?.refresh()} disabled={!localRefresh || localRefresh.busy}>{localRefresh?.busy ? "正在同步…" : "立即同步"}</Button></div></section>
    {localRefresh?.error && <p className="usage-error" role="status">{localRefresh.error}。仍显示最近一次成功读取的数据。</p>}
    <section className="usage-filter-surface" aria-label="时间与用量筛选">
      <TimeRangePicker value={applied} followNow={followNow} relativeKey={relativeKey} onApply={applyTimeRange} />
      <div className="usage-view-controls">
        <div className="usage-control-group"><span>粒度</span><SegmentedControl aria-label="时间粒度" value={granularity} onChange={setGranularity} options={[{value:"1h",label:"1 小时"},{value:"6h",label:"6 小时"},{value:"12h",label:"12 小时"},{value:"day",label:"天"}]} /></div>
        <SessionPicker options={result?.options || []} selection={selection} disabled={loading} onChange={setSelection} />
        <div className="usage-control-group usage-color-mode"><span>颜色</span><SegmentedControl value={mode} onChange={setMode} options={[{value:"effort",label:"推理强度"},{value:"composition",label:"用量构成"}]} /></div>
      </div>
      <div className="usage-secondary-controls"><div className="usage-live-options"><Switch label="包含上下文压缩" checked={includeCompaction} onChange={setIncludeCompaction} size="compact" /><Switch label="结束时间跟随现在" checked={followNow} onChange={toggleFollow} size="compact" /></div><div><span>纵轴</span><SegmentedControl size="compact" value={axis} onChange={setAxis} options={[{value:"unified",label:"统一"},{value:"independent",label:"独立"}]} /></div></div>
    </section>
    {notice && <div className="usage-notice" role="status">{notice}</div>}
    {includesLegacyHistory && <div className="usage-notice" role="status">早期累计用量已按观测区间估算，计入合计图；明细与估算分别着色。分模型图仅含可归属的逐响应明细，不强行分配旧日志的模型或推理强度。</div>}
    {error && <div className="usage-error" role="alert">{error}<Button onClick={() => setApplied(a => ({ ...a }))}>重试</Button>{error.includes("重新载入") && <Button onClick={() => window.location.reload()}>重新载入</Button>}</div>}
    {!result && loading ? <div className="usage-loading" role="status"><span />正在汇总所选区间…</div> : result && <div className="usage-results" aria-busy={loading || result.generation !== index.generation} inert={result.queryKey !== queryKey ? true : undefined}>
      <div className="usage-summary" data-reviewed-rows><div><span>所选范围总量</span><strong title={number(total)}>{number(total)}</strong><span>tokens</span></div><p>{result.selected_ids.length} / {result.options.length} 个会话<span>·</span>{result.models.length} 个模型<span>·</span>{number(result.response_count)} 次响应</p></div>
        {result.bins.length > 24 && <div className="usage-zoom"><RangeSlider label="图表显示范围（不改变区间合计）" min={0} max={result.bins.length - 1} step={1} value={zoom} onChange={setZoom} formatValue={i => result.bins[Math.round(i)]?.label || ""} /><button onClick={() => setZoom([0,result.bins.length - 1])}>重置视图</button></div>}
      {plotResult.overviewStride > 1 && <p className="usage-overview-note" role="status">长区间概览：每柱合并最多 {plotResult.overviewStride} 个所选时段，悬浮可看完整起止时间。缩小上方显示范围后恢复所选粒度；区间总量不变。</p>}
      <TotalUsageChart result={plotResult} mode={mode} />
      <QuotaCharts result={plotResult} zoom={plotZoom} sourceRows={result.quotaEvidenceRows} />
      {result.models.length ? <>

        <SortableRegion id="usage-models" variant="freeform" className="usage-models" authoredRevision={2}>
          {plotResult.models.map(model => <ModelChart id={"model-" + model.name.replace(/[^a-zA-Z0-9_-]/g, "-")} key={model.name + mode} model={model} result={plotResult} mode={mode} axis={axis} rawRows={result.evidenceRows} />)}
        </SortableRegion>
      </> : <div className="usage-empty"><h2>{selection.mode === "explicit" && !selection.ids.length ? "尚未选择会话" : includesLegacyHistory ? "这个区间没有可归属模型的逐响应明细" : "这个区间没有匹配记录"}</h2><p>{selection.mode === "explicit" ? "选择一个或多个会话，或恢复全选。" : includesLegacyHistory ? "旧日志的区间估算已显示在上方合计图；原始观测区间可展开查看。" : "尝试调整时间范围，再应用查询。"}</p>{selection.mode === "explicit" && <Button onClick={() => setSelection({ mode: "all", ids: [] })}>全选当前时间候选</Button>}</div>}
    </div>}
    <footer className="usage-footer"><div><span className="usage-source-dot" />本机日志<span>最近检查 {checkTime} · 数据截止 {snapshotTime}</span></div><details><summary>数据范围与统计说明</summary><div className="usage-coverage">
      <p>读取 {index.file_count} 个文件，其中 {index.compressed_file_count || 0} 个压缩日志；索引 {number(index.record_count)} 条唯一响应、{number(index.quota_record_count)} 条额度快照。仅覆盖本机保留的日志。</p>
      <p>逐响应明细起于 {coverageDate(index.first)}；额度快照起于 {coverageDate(index.quota_first)}。</p>
      {index.legacy_only_files > 0 && <p>{index.legacy_only_files} 个日志只有旧版累计用量（{coverageDate(index.legacy_first)} ～ {coverageDate(index.legacy_latest)}），未冒充逐响应明细。这些记录已作为区间估算计入合计图，不猜测模型和推理强度。</p>}
      {index.compressed_file_count > 0 && !index.zstd_available && <p>本机未找到 zstd 工具，压缩日志尚未纳入统计。</p>}
      {index.scan?.unreadable_files > 0 && <p>本轮有 {index.scan.unreadable_files} 个文件无法读取，保留之前成功索引的记录，稍后重试。</p>}
      <p>{index.index_coverage?.available ? index.index_coverage.missing_log_files ? `聊天索引中 ${index.index_coverage.missing_log_files} 个源日志路径缺失（已检查压缩存档）；缺失历史无法重建。` : "聊天索引关联的日志均已找到，压缩存档不计为缺失。" : "聊天标题索引不可用时，使用会话 ID 显示。"}</p>
      <p>总量＝输入＋输出。缓存输入已在输入内，推理输出已在输出内。推理强度占比按本模型全区间总 Token 计算，未知强度仍计入分母。</p><p>合计图包含单独着色的旧日志区间估算；输入、输出等明细字段仅含逐响应记录，旧累计未知的压缩类型不随压缩开关排除。模型和强度为日志配置。空白时间段表示现存日志未找到记录；不能认定完整账户在该时段零消耗。整次响应按其记录时间归属。</p><p>最后刷新 {new Date(index.updated_at).toLocaleString("zh-CN", { timeZone: applied.timezone, hour12:false })} · 跳过异常 {index.scan?.invalid_records || 0} · 冲突响应 {index.conflicting_responses || 0}</p>{result?.warnings?.map(w => <p key={w}>{w}</p>)}
    </div></details></footer>
  </article>;
}
