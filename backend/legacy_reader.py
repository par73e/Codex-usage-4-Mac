#!/usr/bin/env python3
"""Read local Codex token_usage_record events; never modify source logs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VERSION = "1.0.0"
FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
)
CONFIG_PATH = Path(__file__).resolve().with_name("config.json")


@dataclass(frozen=True)
class Record:
    response_id: str
    timestamp: datetime
    usage: tuple[int, ...]
    compaction: bool = False

    @property
    def values(self) -> dict[str, int]:
        return dict(zip(FIELDS, self.usage))


@dataclass
class Scan:
    records: dict[str, Record] = field(default_factory=dict)
    conflicts: set[str] = field(default_factory=set)
    counts: Counter = field(default_factory=Counter)
    issues: list[dict[str, Any]] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    def issue(self, kind: str, path: Path, line: int = 0) -> None:
        self.counts[kind] += 1
        if len(self.issues) < 20:
            self.issues.append({"kind": kind, "file": str(path), "line": line})

    def add(self, record: Record) -> None:
        self.counts["raw_records"] += 1
        key = record.response_id
        if key in self.conflicts:
            self.counts["conflicting_rows"] += 1
            return
        previous = self.records.get(key)
        if previous is None:
            self.records[key] = record
        elif (previous.timestamp, previous.usage) == (record.timestamp, record.usage):
            self.counts["duplicate_records"] += 1
            # A truncated copy may miss the following compaction marker.
            if record.compaction and not previous.compaction:
                self.records[key] = record
        else:
            self.records.pop(key)
            self.conflicts.add(key)
            self.counts["conflicting_response_ids"] += 1


def parse_zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value:
        raise ValueError("时区必须是非空字符串，例如 Asia/Shanghai。")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"无效时区：{value}。例如 Asia/Shanghai、UTC、America/New_York。") from None


def parse_minute(value: str, zone: ZoneInfo) -> datetime:
    """Reject ambiguous/nonexistent DST wall times instead of silently guessing."""
    try:
        naive = datetime.strptime(value.replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(f"时间格式不正确：{value}。请使用 YYYY-MM-DD HH:MM。") from None
    candidates = {
        naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        for fold in (0, 1)
        if naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        .astimezone(zone).replace(tzinfo=None) == naive
    }
    if not candidates:
        raise ValueError(f"{value} 在 {zone.key} 因夏令时跳时而不存在，请换一个时间。")
    if len(candidates) > 1:
        raise ValueError(f"{value} 在 {zone.key} 因夏令时回拨出现两次；请换用 UTC 指定该区间。")
    return candidates.pop()


def parse_record(payload: dict, timestamp: Any) -> Record:
    response_id = payload.get("response_id")
    usage = payload.get("usage")
    if not isinstance(response_id, str) or not response_id.strip() or not isinstance(usage, dict):
        raise ValueError("missing_id_or_usage")
    if "input_tokens" not in usage or "output_tokens" not in usage or "total_tokens" not in usage:
        raise ValueError("missing_usage_fields")
    values = tuple(usage.get(key, 0) for key in FIELDS)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("invalid_usage_values")
    u = dict(zip(FIELDS, values))
    if (u["total_tokens"] != u["input_tokens"] + u["output_tokens"]
            or u["cached_input_tokens"] > u["input_tokens"]
            or u["reasoning_output_tokens"] > u["output_tokens"]):
        raise ValueError("inconsistent_usage")
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError("invalid_timestamp") from None
    return Record(response_id, instant.astimezone(timezone.utc), values)


def snapshot_lines(path: Path, scan: Scan) -> Iterator[tuple[int, bytes, bool]]:
    """Bound each read by its initial size; tolerate an in-progress final line."""
    try:
        with path.open("rb") as stream:
            import os
            size = os.fstat(stream.fileno()).st_size
            remaining = size
            tail = b""
            number = 0
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    scan.issue("files_truncated_during_scan", path)
                    break
                remaining -= len(block)
                parts = (tail + block).split(b"\n")
                tail = parts.pop()
                for line in parts:
                    number += 1
                    yield number, line, False
            if tail:
                number += 1
                yield number, tail, True
            if os.fstat(stream.fileno()).st_size != size:
                scan.counts["files_changed_during_scan"] += 1
    except OSError:
        scan.issue("unreadable_files", path)


def scan_logs(root: Path) -> Scan:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"日志根目录不存在：{root}")
    scan = Scan()
    for folder in (root / "sessions", root / "archived_sessions"):
        if folder.is_dir():
            scan.files.extend(folder.rglob("*.jsonl"))
    scan.files = sorted(set(scan.files))
    if not scan.files:
        raise ValueError(f"{root} 下的 sessions / archived_sessions 中没有 JSONL 日志。")
    for path in scan.files:
        pending: Record | None = None
        valid_records = 0
        legacy_counts = 0
        for number, raw, is_tail in snapshot_lines(path, scan):
            if not raw.strip():
                continue
            # Only parse usage events and the line after a usage event.
            interesting = b'"token_usage_record"' in raw or b'"token_count"' in raw
            if not interesting and pending is None:
                continue
            try:
                event = json.loads(raw)
                if not isinstance(event, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                scan.issue("partial_tail_lines" if is_tail else "malformed_candidate_lines", path, number)
                if pending is not None:
                    scan.add(pending)
                    pending = None
                continue
            if pending is not None:
                if event.get("type") == "compacted":
                    pending = Record(pending.response_id, pending.timestamp, pending.usage, True)
                scan.add(pending)
                pending = None
            kind = event.get("type")
            payload = event.get("payload")
            if not isinstance(payload, dict):
                if kind == "token_usage_record":
                    scan.issue("missing_payload", path, number)
                continue
            if kind == "token_usage_record":
                try:
                    pending = parse_record(payload, event.get("timestamp"))
                    valid_records += 1
                except ValueError as exc:
                    scan.issue(str(exc), path, number)
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
                    legacy_counts += 1
        if pending is not None:
            scan.add(pending)
        if valid_records:
            scan.counts["files_with_records"] += 1
        elif legacy_counts:
            scan.counts["legacy_only_files"] += 1
        else:
            scan.counts["files_without_records"] += 1
    return scan


def index_coverage(root: Path) -> dict[str, Any]:
    db = root.expanduser().resolve() / "state_5.sqlite"
    if not db.is_file():
        return {"available": False}
    try:
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute("SELECT rollout_path, tokens_used FROM threads").fetchall()
        compressed = [(p,n) for p,n in rows if not Path(p).is_file() and Path(p+'.zst').is_file()]
        missing = [(p, n) for p, n in rows if not Path(p).is_file() and not Path(p+'.zst').is_file()]
        return {"available": True, "indexed_threads": len(rows), "missing_log_files": len(missing),
                "compressed_log_files": len(compressed),
                "missing_logs_with_nonzero_counter": sum(n > 0 for _, n in missing)}
    except (sqlite3.Error, OSError, ValueError):
        return {"available": False, "reason": "本地索引不可读；日志查询仍已完成。"}


def bucket_name(instant: datetime, zone: ZoneInfo, group: str) -> str:
    local = instant.astimezone(zone)
    if group == "day":
        return local.strftime("%Y-%m-%d")
    # Include offset to keep repeated hours during DST fall-back distinct.
    return local.strftime("%Y-%m-%d %H:00 %z" if group == "hour" else "%Y-%m-%d %H:%M %z")


def query(scan: Scan, start: datetime, end: datetime, zone: ZoneInfo,
          group: str = "day", exclude_compaction: bool = False,
          now: datetime | None = None) -> dict[str, Any]:
    if end <= start:
        raise ValueError("结束时间必须晚于起始时间。")
    now = now or datetime.now(timezone.utc)
    totals = Counter({key: 0 for key in (*FIELDS, "response_count", "compaction_tokens")})
    groups: dict[str, Counter] = defaultdict(Counter)
    selected: list[Record] = []
    excluded = 0
    compaction_count = 0
    for record in sorted(scan.records.values(), key=lambda r: (r.timestamp, r.response_id)):
        if not start <= record.timestamp < end:
            continue
        if record.compaction and exclude_compaction:
            excluded += 1
            continue
        selected.append(record)
        u = record.values
        totals.update(u)
        totals["response_count"] += 1
        if record.compaction:
            compaction_count += 1
            totals["compaction_tokens"] += u["total_tokens"]
        if group != "none":
            label = bucket_name(record.timestamp, zone, group)
            groups[label].update(u)
            groups[label]["response_count"] += 1
            if record.compaction:
                groups[label]["compaction_tokens"] += u["total_tokens"]
    totals["uncached_input_tokens"] = totals["input_tokens"] - totals["cached_input_tokens"]
    all_times = [r.timestamp for r in scan.records.values()]
    earliest = min(all_times) if all_times else None
    latest = max(all_times) if all_times else None
    warnings = ["仅统计本机保留的响应级日志；区间内没有记录不等于账户用量为零。"]
    if end > now:
        warnings.append("结束时间在未来：只能统计扫描时已经写入日志的用量。")
    if earliest and start < earliest:
        warnings.append("起始时间早于现存记录的最早时间，之前的用量不可由这些日志重建。")
    if latest and end > latest:
        warnings.append("查询终点晚于现存记录的最新时间；最新记录之后的用量未包含，不能据此判断是否有活动。")
    if scan.conflicts:
        warnings.append(f"发现 {len(scan.conflicts)} 个内容冲突的响应 ID，已整体排除，避免任选一份。")
    if scan.counts["legacy_only_files"]:
        warnings.append(f"{scan.counts['legacy_only_files']} 个日志只有旧累计事件，没有响应级明细，未计入。")
    problem_count = sum(scan.counts[key] for key in (
        "malformed_candidate_lines", "unreadable_files", "missing_id_or_usage", "missing_payload",
        "missing_usage_fields", "invalid_usage_values", "inconsistent_usage", "invalid_timestamp",
        "files_truncated_during_scan",
    ))
    if problem_count:
        warnings.append(f"有 {problem_count} 处读取或格式异常被跳过，详见 JSON 输出的 issues。")
    if scan.counts["partial_tail_lines"]:
        warnings.append("部分文件最后一行尚未写完整，该行暂未计入；稍后重新查询可读取完成后的记录。")
    def show(value: datetime | None) -> str | None:
        return value.astimezone(zone).isoformat(timespec="milliseconds") if value else None
    return {
        "version": VERSION, "timezone": zone.key,
        "interval": {"start_inclusive": show(start), "end_exclusive": show(end)},
        "group_by": group, "includes_compaction": not exclude_compaction,
        "totals": dict(totals), "compaction_response_count": compaction_count,
        "excluded_compaction_responses": excluded,
        "groups": [{"period": label, **dict(values)} for label, values in groups.items()],
        "matched_records": {"first": show(selected[0].timestamp) if selected else None,
                            "last": show(selected[-1].timestamp) if selected else None},
        "source": {"files_scanned": len(scan.files), "unique_response_records": len(scan.records),
                   "earliest_record": show(earliest), "latest_record": show(latest),
                   "counters": dict(scan.counts), "issues": scan.issues,
                   "captured_at": show(now)},
        "warnings": warnings,
        "accounting": {
            "total": "input_tokens + output_tokens",
            "cached_input": "已包含在 input_tokens 中，不重复加到总量。",
            "reasoning_output": "已包含在 output_tokens 中，不重复加到总量。",
            "timestamp": "整次响应按 token_usage_record 时间戳归日；无法拆分跨午夜响应。",
            "compaction": "根据紧随 token_usage_record 的 compacted 事件识别压缩调用。",
        },
    }


def text_result(result: dict, limit: int = 100) -> str:
    t, source = result["totals"], result["source"]
    lines = [
        "本地 Token 用量", f"时区：{result['timezone']}",
        f"区间：{result['interval']['start_inclusive']}  ≤ 时间 <  {result['interval']['end_exclusive']}",
        "", f"总 token：       {t.get('total_tokens', 0):,}",
        f"输入：          {t.get('input_tokens', 0):,}",
        f"  缓存输入：    {t.get('cached_input_tokens', 0):,}（已包含在输入中）",
        f"  非缓存输入：  {t.get('uncached_input_tokens', 0):,}",
        f"输出：          {t.get('output_tokens', 0):,}",
        f"  推理输出：    {t.get('reasoning_output_tokens', 0):,}（已包含在输出中）",
        f"响应次数：      {t.get('response_count', 0):,}",
        f"上下文压缩：    {'包含' if result['includes_compaction'] else '排除'}"
        f"（计入 {result['compaction_response_count']} 次，{t.get('compaction_tokens', 0):,} token；"
        f"排除 {result['excluded_compaction_responses']} 次）",
    ]
    groups = result["groups"]
    if groups:
        lines += ["", f"{'时间段':<28} {'响应':>7} {'输入':>15} {'缓存输入':>15} {'输出':>12} {'总 token':>15}"]
        for row in groups[:limit]:
            lines.append(f"{row['period']:<28} {row.get('response_count', 0):>7,} "
                         f"{row.get('input_tokens', 0):>15,} {row.get('cached_input_tokens', 0):>15,} "
                         f"{row.get('output_tokens', 0):>12,} {row.get('total_tokens', 0):>15,}")
        if len(groups) > limit:
            lines.append(f"共 {len(groups)} 个有记录的时间段；只显示前 {limit} 个。使用 --limit 或 --format json 查看更多。")
        lines.append("未列出的时间段未找到匹配记录；不补成账户零用量。")
    else:
        if not t.get("response_count"):
            lines += ["", "这个区间未找到匹配的现存记录。"]
    lines += ["", f"扫描：{source['files_scanned']} 个文件，{source['unique_response_records']:,} 个唯一响应。",
              f"现存记录跨度：{source['earliest_record'] or '无'} 至 {source['latest_record'] or '无'}"]
    coverage = source.get("index_coverage", {})
    if coverage.get("available"):
        lines.append(f"聊天索引：{coverage['indexed_threads']} 个条目，"
                     f"{coverage['missing_log_files']} 个日志原路径缺失（其中 "
                     f"{coverage['missing_logs_with_nonzero_counter']} 个有非零累计量）。")
    lines += ["", "范围说明：", *[f"  · {warning}" for warning in result["warnings"]],
              "  · 按整次响应的记录时间归属；这不是账单金额或额度消耗。"]
    return "\n".join(lines)


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError()
        for key in ("timezone", "log_root"):
            if key in data and (not isinstance(data[key], str) or not data[key].strip()):
                raise ValueError()
        return data
    except (OSError, ValueError):
        raise ValueError(f"配置文件无法读取：{path}。请检查 JSON 格式。") from None


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="按任意分钟区间与时区查询本地 Codex token 日志。只读源日志。",
                                epilog='示例：python3 token_usage.py --start "2026-10-23 01:30" --end "2026-10-26 12:00"')
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--start", help="起始分钟，包含，格式 YYYY-MM-DD HH:MM")
    p.add_argument("--end", help="结束分钟，不包含，格式 YYYY-MM-DD HH:MM")
    p.add_argument("--timezone", "--tz", help="IANA 时区，例如 Asia/Shanghai、UTC；默认读取配置")
    p.add_argument("--log-root", type=Path, help="日志根目录，包含 sessions / archived_sessions；默认 ~/.codex")
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help="配置文件；默认程序旁边的 config.json")
    p.add_argument("--set-timezone", metavar="TIMEZONE", help="保存默认时区后退出；不会修改 ChatGPT 设置")
    p.add_argument("--group-by", choices=["none", "minute", "hour", "day"], default="day", help="分组粒度，默认 day")
    p.add_argument("--exclude-compaction", action="store_true", help="排除可识别的上下文压缩调用")
    p.add_argument("--format", choices=["text", "json"], default="text", help="输出格式，默认 text")
    p.add_argument("--limit", type=int, default=100, help="文本输出最多显示的分组数，默认 100；不影响汇总和 JSON")
    p.add_argument("--output", type=Path, help="另存查询结果到文件；已有文件不会被覆盖")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.set_timezone:
            zone = parse_zone(args.set_timezone)
            if args.start or args.end:
                raise ValueError("--set-timezone 仅用于保存设置；查询请使用 --timezone。")
            config["timezone"] = zone.key
            args.config.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"默认时区已设为 {zone.key}。配置：{args.config}")
            return 0
        if not args.start or not args.end:
            parser.error("查询时必须同时提供 --start 和 --end。")
        if args.limit < 1:
            raise ValueError("--limit 必须大于 0。")
        zone = parse_zone(args.timezone or config.get("timezone", "Asia/Shanghai"))
        start, end = parse_minute(args.start, zone), parse_minute(args.end, zone)
        if end <= start:
            raise ValueError("结束时间必须晚于起始时间。")
        root = (args.log_root or Path(config.get("log_root", "~/.codex"))).expanduser().resolve()
        scan = scan_logs(root)
        result = query(scan, start, end, zone, args.group_by, args.exclude_compaction)
        result["source"]["log_root"] = str(root)
        result["source"]["index_coverage"] = index_coverage(root)
        if result["source"]["index_coverage"].get("missing_log_files", 0):
            result["warnings"].append("本地聊天索引中有日志原路径缺失，因此无法重建完整历史。")
        output = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else text_result(result, args.limit)
        if args.output:
            with args.output.expanduser().open("x", encoding="utf-8") as destination:
                destination.write(output + "\n")
            print(f"已保存：{args.output.expanduser()}", file=sys.stderr)
        print(output)
        return 0
    except (ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("查询已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
