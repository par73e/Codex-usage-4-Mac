import { useCallback, useEffect, useMemo, useRef, useState } from "react";

function generation(snapshot) {
  return snapshot.queries?.index_state?.rows?.[0]?.generation;
}

async function jsonRequest(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(path, { cache: "no-store", ...options, signal: controller.signal });
    const value = await response.json();
    if (!response.ok) throw new Error(value.refresh_error || value.error || "本机服务暂时不可用");
    return value;
  } finally { clearTimeout(timer); }
}

// Poll only lightweight status. Download reviewed rows atomically when their
// generation changes; the chart and source inspector then see the same data.
export function useLiveUsage(initialSnapshot, enabled) {
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [state, setState] = useState({ connected: false, busy: false, error: "", checkedAt: null });
  const currentGeneration = useRef(null), pending = useRef(null), mounted = useRef(false);
  const synchronize = useCallback(async (force = false) => {
    if (!enabled) return;
    if (pending.current) {
      await pending.current;
      if (!force) return;
    }
    const request = (async () => {
      if (mounted.current && force) setState(s => ({ ...s, busy: true }));
      try {
        const status = await jsonRequest(force ? "/api/v1/index/refresh" : "/api/v1/status",
          force ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" } : {});
        if (status.refresh_error) throw new Error(status.refresh_error);
        if (status.generation !== currentGeneration.current) {
          const next = await jsonRequest("/api/v1/snapshot");
          if (next.id !== initialSnapshot.id || !generation(next) || !Array.isArray(next.queries?.usage?.rows))
            throw new Error("本机数据版本不完整，稍后自动重试");
          if (mounted.current) {
            currentGeneration.current = generation(next);
            setSnapshot(next);
          }
        }
        if (mounted.current) setState({ connected: true, busy: false, error: "", checkedAt: status.checked_at, interval: status.poll_interval });
      } catch (error) {
        if (mounted.current) setState(s => ({ ...s, connected: false, busy: false,
          error: error.name === "AbortError" ? "本机服务响应超时，将自动重试" : "同步中断：" + error.message + "；将自动重试" }));
      }
    })();
    pending.current = request;
    try { await request; } finally { if (pending.current === request) pending.current = null; }
  }, [enabled, initialSnapshot.id]);

  useEffect(() => {
    if (!enabled) return;
    mounted.current = true;
    let timer, active = true;
    const tick = async () => {
      if (document.visibilityState !== "hidden") await synchronize();
      if (active) timer = setTimeout(tick, 2000);
    };
    const visible = () => { if (document.visibilityState === "visible") void synchronize(); };
    void tick();
    document.addEventListener("visibilitychange", visible);
    return () => { active = false; mounted.current = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [enabled, synchronize]);

  const refresh = useCallback(() => synchronize(true), [synchronize]);
  const reconcile = useCallback(() => synchronize(false), [synchronize]);
  const control = useMemo(() => enabled ? { ...state, refresh, reconcile } : undefined, [enabled, state, refresh, reconcile]);
  return { snapshot, control };
}
