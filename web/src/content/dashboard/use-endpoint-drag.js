import { useEffect, useRef, useState } from "react";
import { displayMinute, moveEndpointDay } from "./date-range.js";

// A short click remains an edit action; only a stationary long press starts
// capture. Capture belongs to the stable control root, not a moving date cell.
export function useEndpointDrag({ root, open, range, onPreview, onEdit, onMonth }) {
  const latest = useRef(); latest.current = { range, onPreview, onEdit, onMonth };
  const gesture = useRef(null), hold = useRef(null), edge = useRef(null), blockedClick = useRef(false);
  const [drag, setDrag] = useState(null);
  function stopEdge() { if (edge.current) clearInterval(edge.current.timer); edge.current = null; }
  function release() {
    clearTimeout(hold.current); stopEdge();
    const current = gesture.current;
    if (current && root.current?.hasPointerCapture?.(current.id)) root.current.releasePointerCapture(current.id);
    gesture.current = null;
  }
  function begin(event, index) {
    if (event.button !== 0 || !latest.current.range.every(Boolean)) return;
    release(); blockedClick.current = false;
    const current = { id: event.pointerId, index, x: event.clientX, y: event.clientY,
      phase: "pending", base: [...latest.current.range] };
    gesture.current = current;
    hold.current = setTimeout(() => {
      if (gesture.current !== current) return;
      current.phase = "dragging"; blockedClick.current = true;
      root.current?.setPointerCapture?.(current.id);
      latest.current.onEdit(index);
      setDrag({ active: true, index, text: `正在拖动${index ? "结束" : "开始"}时间 · ${displayMinute(current.base[index])}` });
    }, 100);
  }
  useEffect(() => {
    if (!open) { release(); setDrag(null); return; }
    const move = event => {
      const current = gesture.current;
      if (!current || current.id !== event.pointerId) return;
      if (current.phase === "pending") {
        if (Math.hypot(event.clientX - current.x, event.clientY - current.y) > 7) release();
        return;
      }
      event.preventDefault();
      const cell = document.elementFromPoint(event.clientX, event.clientY)?.closest("td[title]");
      const day = cell?.getAttribute("title");
      if (cell && root.current?.contains(cell) && /^\d{4}-\d{2}-\d{2}$/.test(day || "") && day !== current.lastDay) {
        current.lastDay = day;
        const next = moveEndpointDay(current.base, current.index, day);
        if (next) {
          latest.current.onPreview(next);
          setDrag({ active: true, index: current.index, text: `正在拖动${current.index ? "结束" : "开始"}时间 · ${displayMinute(next[current.index])}` });
        } else setDrag({ active: true, index: current.index, invalid: true, text: "首尾端点不能互相越过" });
      }
      const body = root.current?.querySelector(".usage-range-popup .ant-picker-date-panel .ant-picker-body");
      const bounds = body?.getBoundingClientRect();
      const direction = bounds && event.clientY >= bounds.top && event.clientY <= bounds.bottom
        ? event.clientX >= bounds.left - 12 && event.clientX < bounds.left + 10 ? -1
          : event.clientX > bounds.right - 10 && event.clientX <= bounds.right + 12 ? 1 : 0 : 0;
      if (edge.current?.direction !== direction) {
        stopEdge();
        if (direction) edge.current = { direction, timer: setInterval(() => latest.current.onMonth(direction), 650) };
      }
    };
    const finish = event => {
      const current = gesture.current;
      if (!current || current.id !== event.pointerId) return;
      if (current.phase === "dragging") setDrag({ active: false, index: current.index, text: "日期已调整，可继续改时分，点击「应用时间范围」生效" });
      release();
    };
    const cancel = () => {
      if (gesture.current?.phase === "dragging") latest.current.onPreview(gesture.current.base);
      release(); blockedClick.current = false; setDrag(null);
    };
    const key = event => { if (event.key === "Escape") cancel(); };
    const click = event => {
      if (blockedClick.current) { event.preventDefault(); event.stopPropagation(); blockedClick.current = false; }
    };
    document.addEventListener("pointermove", move, { passive: false });
    document.addEventListener("pointerup", finish);
    document.addEventListener("pointercancel", cancel);
    document.addEventListener("keydown", key);
    root.current?.addEventListener("click", click, true);
    const node = root.current;
    return () => {
      release();
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", finish);
      document.removeEventListener("pointercancel", cancel);
      document.removeEventListener("keydown", key);
      node?.removeEventListener("click", click, true);
    };
  }, [open]);
  return { begin, drag };
}
