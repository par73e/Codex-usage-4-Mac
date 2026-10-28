import React, { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ConfigProvider, DatePicker, theme } from "antd";
import zhCN from "antd/es/locale/zh_CN";
import dayjs from "dayjs";
import utc from "dayjs/plugin/utc";
import "dayjs/locale/zh-cn";
import { RANGE_PRESETS, displayMinute, localMinute, presetRange, rangeError, rememberRange, restoreHistory, zoneLabel } from "./date-range.js";
import "./time-range.css";
import { useEndpointDrag } from "./use-endpoint-drag.js";

dayjs.extend(utc);
const TZ = [...new Set(["Asia/Shanghai", "UTC", ...(typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : ["Asia/Tokyo", "Europe/London", "America/New_York", "America/Los_Angeles"])])];
const readScheme = () => document.documentElement.dataset.colorScheme || "light";
function subscribeScheme(callback) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-color-scheme"] });
  return () => observer.disconnect();
}
// Treat picker values as wall-clock fields. The backend resolves them in the
// explicitly selected IANA zone, rather than silently using the browser's zone.
const pickerValue = range => [dayjs.utc(range.start).locale("zh-cn"), dayjs.utc(range.end).locale("zh-cn")];
const completeDates = value => Array.isArray(value) && value.length === 2 && value.every(date => date?.isValid?.());

export function TimeRangePicker({ value, followNow, relativeKey, onApply }) {
  const root = useRef(null), picker = useRef(null);
  const [open, setOpen] = useState(false), [error, setError] = useState("");
  const [dates, setDates] = useState(() => pickerValue(value));
  const [preview, setPreview] = useState(() => pickerValue(value));
  const [editing, setEditing] = useState(0), [panelMonth, setPanelMonth] = useState(() => pickerValue(value));
  const draft = completeDates(preview) ? preview : dates;
  const editEndpoint = index => {
    setEditing(index);
    requestAnimationFrame(() => { picker.current?.focus({ index }); setEditing(index); });
  };
  const { begin, drag } = useEndpointDrag({ root, open, range: draft.map(date => date?.format("YYYY-MM-DDTHH:mm")),
    onEdit: editEndpoint,
    onPreview: next => { const result = next.map(date => dayjs.utc(date).locale("zh-cn")); setDates(result); setPreview(result); },
    onMonth: direction => setPanelMonth(current => {
      const base = current?.[editing] || draft[editing];
      if (!base) return current;
      const next = base.add(direction, "month"); return [next, next];
    }),
  });
  const [history, setHistory] = useState(() => { try { return restoreHistory(JSON.parse(localStorage.getItem("codex-usage:recent-ranges") || "[]")); } catch { return []; } });
  const scheme = useSyncExternalStore(subscribeScheme, readScheme, () => "light");
  const dark = scheme === "dark";
  const antTheme = useMemo(() => ({
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: { fontFamily: "inherit", fontSize: 13, borderRadius: 8, controlHeight: 42,
      colorPrimary: dark ? "#8dbbff" : "#246fe6", colorBgContainer: dark ? "#28292d" : "#ffffff",
      colorBgElevated: dark ? "#292a2e" : "#ffffff", colorText: dark ? "#eff1f5" : "#202631",
      colorTextSecondary: dark ? "#a7afbc" : "#626e7f", colorBorder: dark ? "#43464e" : "#d7dce4" },
  }), [dark]);
  useEffect(() => { if (!open) { const next = pickerValue(value); setDates(next); setPreview(next); setPanelMonth(next); } }, [open, value.start, value.end, value.timezone]);
  useEffect(() => {
    if (!open) return;
    const outside = event => { if (!root.current?.contains(event.target)) setOpen(false); };
    const escape = event => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  useEffect(() => { try { localStorage.setItem("codex-usage:recent-ranges", JSON.stringify(history)); } catch {} }, [history]);

  function commit(next, nextFollow = false, nextRelative = null) {
    const issue = rangeError(next);
    if (issue) { setError(issue); return; }
    onApply(next, nextFollow, nextRelative);
    setHistory(current => rememberRange(current, { ...next, followNow: nextFollow, relativeKey: nextRelative }));
    setError(""); setOpen(false); setDates(pickerValue(next));
  }
  function choosePreset(key) { commit(presetRange(key, value.timezone), key !== "yesterday", key === "yesterday" ? null : key); }
  function chooseRecent(item) {
    const next = item.relativeKey ? presetRange(item.relativeKey, item.timezone)
      : { ...item, end: item.followNow ? localMinute(new Date(Date.now() + 60000), item.timezone) : item.end };
    commit(next, Boolean(item.followNow), item.relativeKey || null);
  }
  const activeLabel = RANGE_PRESETS.find(preset => preset.key === relativeKey)?.label || (followNow ? "固定起点 · 截至现在" : "自定义时间范围");
  const locale = useMemo(() => ({ ...zhCN, DatePicker: { ...zhCN.DatePicker, lang: { ...zhCN.DatePicker.lang, ok: "应用时间范围" } } }), []);

  function renderCell(current, info) {
    if (info.type !== "date") return info.originNode;
    const endpoints = draft.map((date, index) => date?.isSame(current, "day") ? index : null).filter(index => index !== null);
    if (!endpoints.length) return info.originNode;
    const index = endpoints.length === 1 ? endpoints[0] : editing;
    return React.cloneElement(info.originNode, {
      className: `${info.originNode.props.className || ""} usage-date-endpoint`,
      role: "button", tabIndex: 0, "aria-label": `编辑${index ? "结束" : "开始"}端点 ${current.format("YYYY-MM-DD")}，长按可拖动`,
      onPointerDown: event => begin(event, index),
      onMouseDown: event => event.preventDefault(),
      onContextMenu: event => event.preventDefault(),
      onClick: event => { event.preventDefault(); event.stopPropagation(); editEndpoint(index); },
      onKeyDown: event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); event.stopPropagation(); editEndpoint(index); } },
      children: <>{info.originNode.props.children}{endpoints.map(endpoint => <span className={`usage-date-handle usage-date-handle-${endpoint}`} key={endpoint}
        role="button" tabIndex={0} aria-label={`${endpoint ? "结束" : "开始"}日期拖动端点`}
        onPointerDown={event => { event.stopPropagation(); begin(event, endpoint); }}
        onClick={event => { event.preventDefault(); event.stopPropagation(); editEndpoint(endpoint); }}
        onKeyDown={event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); event.stopPropagation(); editEndpoint(endpoint); } }}
      >{endpoint ? "止" : "起"}</span>)}</>,
    });
  }

  return <div className="usage-range-control" ref={root} data-endpoint-dragging={drag?.active || undefined}>
    <div className="usage-range-heading"><span>时间范围</span><span className="usage-range-mode">{activeLabel}</span></div>
    <div className="usage-range-line">
      <label className="usage-range-sr" htmlFor="usage-range-start">开始时间</label><label className="usage-range-sr" htmlFor="usage-range-end">结束时间</label>
      <ConfigProvider locale={locale} theme={antTheme}>
        <DatePicker.RangePicker ref={picker} className="usage-range-input"
          id={{ start: "usage-range-start", end: "usage-range-end" }}
          placeholder={["开始时间", "结束时间"]} value={dates} open={open}
          onOpenChange={next => { if (next) { setError(""); setOpen(true); } }}
          onChange={next => { if (completeDates(next)) { setDates(next); setPreview(next); } }}
          onCalendarChange={next => { setPreview(next); setError(""); }}
          onFocus={(_event, info) => { if (info?.range) setEditing(info.range === "end" ? 1 : 0); }}
          pickerValue={panelMonth}
          onPickerValueChange={next => { if (completeDates(next)) setPanelMonth(next); }}
          cellRender={renderCell}
          format={["YYYY-MM-DD HH:mm", "YYYY/MM/DD HH:mm"]}
          showTime={{ format: "HH:mm", showSecond: false, minuteStep: 1 }}
          needConfirm={false} previewValue={false} allowClear={false} inputReadOnly={false}
          classNames={{ popup: { root: "usage-range-popup" } }}
          getPopupContainer={() => root.current}
          renderExtraFooter={() => <div className="usage-range-footer"><div className={`usage-range-panel-hint${drag?.invalid ? " usage-range-drag-invalid" : ""}`} role="status">{drag?.text || `${zoneLabel(value.timezone)} · 点击端点改时分，长按拖动改日期；描边为今天`}</div>
            <button type="button" className="usage-range-confirm" onMouseDown={event => event.preventDefault()} onClick={event => {
              event.stopPropagation();
              if (draft?.every(Boolean)) commit({ start: draft[0].format("YYYY-MM-DDTHH:mm"), end: draft[1].format("YYYY-MM-DDTHH:mm"), timezone: value.timezone });
            }}>应用时间范围</button></div>}
          panelRender={panel => <div className="usage-range-panel">
            <aside className="usage-range-shortcuts" aria-label="快捷与最近时间范围">
              <div className="usage-range-section-title">快捷范围</div>
              <div className="usage-range-preset-list">{RANGE_PRESETS.map(preset => <button type="button" key={preset.key}
                className={relativeKey === preset.key ? "is-active" : ""}
                onClick={() => choosePreset(preset.key)}>{preset.label}</button>)}</div>
              {history.length > 0 && <details className="usage-range-recent" open><summary>最近使用</summary>
                <div>{history.map((item, index) => <button type="button" key={index} onClick={() => chooseRecent(item)}>
                  <span>{RANGE_PRESETS.find(preset => preset.key === item.relativeKey)?.label || displayMinute(item.start)}</span>
                  <small>{item.relativeKey ? zoneLabel(item.timezone) : `${item.followNow ? "截至现在" : "至 " + displayMinute(item.end)} · ${zoneLabel(item.timezone)}`}</small>
                </button>)}</div>
              </details>}
            </aside>
            <div className="usage-range-calendar"><div className="usage-range-endpoints" role="group" aria-label="选择要编辑的端点" onFocusCapture={event => event.stopPropagation()}>
              {[0, 1].map(index => <button type="button" key={index} aria-pressed={editing === index}
                onMouseDown={event => event.preventDefault()} onClick={event => { event.stopPropagation(); editEndpoint(index); }}>
                <span>{index ? "结束" : "开始"}</span><strong>{draft[index]?.format("MM-DD HH:mm") || "请选择"}</strong>
              </button>)}
            </div>{panel}</div>
          </div>}
        />
      </ConfigProvider>
      <label className="usage-range-zone"><span>时区</span><select aria-label="统计时区" value={value.timezone} onChange={event => {
        const timezone = event.target.value;
        commit(relativeKey ? presetRange(relativeKey, timezone) : { ...value, timezone }, followNow, relativeKey);
      }}>{TZ.map(zone => <option key={zone} value={zone}>{zone === "Asia/Shanghai" ? "北京时间 · UTC+8" : zone}</option>)}</select></label>
    </div>
    <div className="usage-range-help"><span>可直接输入或粘贴完整日期时间，也可点选日历</span><span>起点包含 · 终点不包含</span></div>
    {error && <p className="usage-range-error" role="alert">{error}</p>}
  </div>;
}
