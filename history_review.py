"""Read-only adapters for local Agent history sources, plus shared JSON/time/redaction helpers."""

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


SOURCES = ("codex", "claude", "comate")
MAX_FILE = 128 * 1024 * 1024
# Codex 续跑会新开 rollout 文件，文件名形如 rollout-<时间>-<根会话>_<续跑段>.jsonl，
# 而 session_meta 只带根会话 id。续跑段 id 仅出现在文件名里。
THREAD_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class HistoryError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise HistoryError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"重复 JSON 字段：{key}")
            result[key] = value
        return result
    def constant(value):
        raise HistoryError(f"非法 JSON 数值：{value}")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def timestamp(value):
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return datetime.fromtimestamp(value / 1000 if value > 100000000000 else value, timezone.utc)
        if isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def period(args):
    end = datetime.now(timezone.utc)
    if args.date_from or args.date_to:
        require(args.date_from and args.date_to, "--from 和 --to 必须一起提供")
        require(args.days is None, "日期范围不能与 --days 同时使用")
        try:
            # Interpret each local midnight separately, preserving DST transitions.
            start = datetime.strptime(args.date_from, "%Y-%m-%d").astimezone(timezone.utc)
            end = (datetime.strptime(args.date_to, "%Y-%m-%d") + timedelta(days=1)).astimezone(timezone.utc)
        except ValueError as exc:
            raise HistoryError("日期格式应为 YYYY-MM-DD") from exc
        require(start < end, "开始日期不能晚于结束日期")
        end = min(end, datetime.now(timezone.utc))
    else:
        require(args.days is None or args.days > 0, "days 必须大于 0")
        start = end - timedelta(days=args.days) if args.days else datetime(1970, 1, 1, tzinfo=timezone.utc)
    require(start < end, "日期范围没有已经发生的时间")
    return start, end


def redact(text, secrets=()):
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1[REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    return re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|password|secret)\s*[\"\x27]?\s*[:=]\s*[\"\x27]?)[^\s\"\x27,}]{8,}', r'\1[REDACTED]', text)


def redact_data(value, secrets=()):
    """Redact string values before encoding JSON, preserving escaped-string structure."""
    if isinstance(value,str):return redact(value,secrets)
    if isinstance(value,list):return [redact_data(v,secrets) for v in value]
    if isinstance(value,dict):
        return {k: '[REDACTED]' if re.fullmatch(r'(?i)(?:api[_-]?key|access[_-]?token|password|secret)',k) and isinstance(v,str) and len(v)>=8
                else redact_data(v,secrets) for k,v in value.items()}
    return value


def clean(value):
    """Keep observable messages/tools, not private reasoning or redundant catalogues."""
    if isinstance(value, list):
        return [clean(item) for item in value if not isinstance(item, dict)
                or item.get("type") not in ("thinking", "reasoning", "redacted_thinking")]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()
                if k not in ("thinking", "reasoning", "signature", "encrypted_content", "capabilitySnapshot")}
    return value


def notice(items, source, reason, path=""):
    entry = {"source": source, "path": str(path), "reason": reason}
    if entry not in items:
        items.append(entry)


def blob_expand(value, session_path, problems, budget):
    if isinstance(value, dict):
        if "$blob" in value:
            ref = value["$blob"]
            if not isinstance(ref, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", ref):
                notice(problems, "comate", "不支持的 blob 引用", session_path)
                return "[UNAVAILABLE BLOB]"
            path = session_path.parent / "blobs" / (session_path.name + ".d") / ref[7:]
            try:
                require(path.resolve().is_relative_to(path.parent.resolve()), "blob 软链越出会话资源目录")
                require(value.get("compression", "none") == "none" and value.get("encoding", "utf8") in ("utf8", "json"), "不支持的 blob 编码或压缩")
                size = path.stat().st_size
                require(size <= budget[0], "blob 超出单会话读取预算")
                budget[0] -= size
                raw = path.read_bytes()
                require(hashlib.sha256(raw).hexdigest() == ref[7:], "blob 内容校验失败")
                return strict_json(raw.decode("utf-8")) if value.get("encoding") == "json" else raw.decode("utf-8")
            except (OSError, UnicodeError, ValueError) as exc:
                notice(problems, "comate", str(exc), path)
                return "[UNAVAILABLE BLOB]"
        return {k: blob_expand(v, session_path, problems, budget) for k, v in value.items()}
    if isinstance(value, list):
        return [blob_expand(v, session_path, problems, budget) for v in value]
    return value


def read_session(source, path):
    """Return metadata and observable event candidates; never execute history content."""
    problems, events = [], []
    meta = {"session_id": path.stem, "project": "", "source": source}
    require(path.stat().st_size <= MAX_FILE, "会话文件超过 128 MiB，未读取")
    if source == "comate":
        data = strict_json(path.read_text(encoding="utf-8"))
        require(isinstance(data, dict) and isinstance(data.get("messages"), list), "不支持的 Comate 会话结构")
        meta.update(session_id=str(data.get("sessionUuid") or path.name), project=data.get("workspaceDirectory") or "")
        budget = [MAX_FILE]
        request_times = {m.get("id"): m.get("requestedAt") for m in data["messages"] if isinstance(m, dict)}
        for index, message in enumerate(data["messages"]):
            if not isinstance(message, dict):
                notice(problems, source, "消息不是对象", path)
                continue
            if message.get("discard"):
                continue
            content = {k: message[k] for k in ("role", "status", "content", "elements", "appliedRules", "appliedRuleNames") if k in message}
            content = blob_expand(clean(content), path, problems, budget)
            events.append((f"/messages/{index}", message.get("id"), message.get("requestedAt") or message.get("completedAt") or request_times.get(message.get("userMessageId")), content))
        return meta, events, problems
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                item = strict_json(line)
                require(isinstance(item, dict), "历史行不是对象")
                typ = item.get("type")
                if source == "codex":
                    payload = item.get("payload") or {}
                    require(isinstance(payload, dict), "Codex payload 不是对象")
                    if typ == "session_meta":
                        meta.update(session_id=str(payload.get("id") or payload.get("session_id") or path.stem), project=payload.get("cwd") or "")
                        if payload.get("parent_thread_id"):
                            meta["parent_session"] = str(payload["parent_thread_id"])
                    if typ == "turn_context" and payload.get("cwd"):
                        if meta["project"] and meta["project"] != payload["cwd"]:
                            notice(problems, source, "会话中工作目录发生变化，项目归属可能不完整", path)
                        meta["project"] = meta["project"] or payload["cwd"]
                    if typ == "response_item":
                        ptype = payload.get("type")
                        if ptype == "message" and payload.get("role") not in ("user", "assistant", "developer"):
                            continue
                        if ptype not in ("message", "function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output"):
                            continue
                        events.append((f"line:{line_no}", payload.get("id"), item.get("timestamp"), clean(payload)))
                    elif typ == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted", "compaction"):
                        events.append((f"line:{line_no}", None, item.get("timestamp"), clean(payload)))
                else:
                    meta["session_id"] = str(item.get("sessionId") or meta["session_id"])
                    meta["project"] = item.get("cwd") or meta["project"]
                    if typ in ("user", "assistant"):
                        events.append((f"line:{line_no}", item.get("uuid"), item.get("timestamp"), clean(item.get("message", {}))))
                    elif typ == "attachment" and isinstance(item.get("attachment"), dict):
                        # Keep injected skill attachments, omit unrelated snapshots/catalogues.
                        attachment = item["attachment"]
                        if "skill" in str(attachment.get("type", "")).lower():
                            events.append((f"line:{line_no}", item.get("uuid"), item.get("timestamp"), clean(attachment)))
            except (ValueError, TypeError, KeyError) as exc:
                notice(problems, source, f"第 {line_no} 行无法解析：{type(exc).__name__}", path)
    if source == "claude" and "subagents" in path.parts:
        meta["parent_session"] = meta["session_id"]
        meta["session_id"] += ":subagent:" + path.stem
    return meta, events, problems


def rollout_thread_ids(path):
    """rollout 文件名里出现的全部会话 id：根会话 + 续跑段。

    续跑段在桌面端 SQLite 投影里是独立 thread，若只登记根会话 id，
    同一段对话会被 rollout 与投影各记一次（同一批事件、两条会话）。
    """
    return set(THREAD_ID.findall(Path(path).stem))


def projection_sessions(path, start, end, rollout_ids, problems):
    """Use desktop projections only for sessions without a rollout; never count both."""
    if not path.exists():
        return
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)) as connection:
            connection.execute("PRAGMA query_only=ON")
            ids = connection.execute(
                "SELECT DISTINCT thread_id FROM thread_items WHERE created_at_ms >= ? AND created_at_ms < ? ORDER BY thread_id",
                (int(start.timestamp() * 1000), int(end.timestamp() * 1000))).fetchall()
            for (thread_id,) in ids:
                if thread_id in rollout_ids:
                    continue
                events, projects, size = [], set(), 0
                rows = connection.execute(
                    "SELECT turn_id,item_id,created_at_ms,item_json,item_type FROM thread_items WHERE thread_id=? AND created_at_ms>=? AND created_at_ms<? ORDER BY created_at_ms,rollout_ordinal",
                    (thread_id, int(start.timestamp() * 1000), int(end.timestamp() * 1000)))
                for turn_id, item_id, when, raw, typ in rows:
                    if typ == "reasoning":
                        continue
                    size += len(raw)
                    if size > MAX_FILE:
                        notice(problems, "codex", "SQLite 会话超过读取预算", f"{path}#{thread_id}")
                        break
                    try:
                        item = strict_json(raw)
                        require(isinstance(item, dict), "SQLite 历史消息不是对象")
                        if item.get("cwd"):
                            projects.add(str(Path(item["cwd"]).expanduser().resolve()))
                        events.append((f"thread_items:{thread_id}/{turn_id}/{item_id}", item_id, when, clean(item)))
                    except (ValueError, TypeError):
                        notice(problems, "codex", "SQLite 消息无法解析", f"{path}#{thread_id}/{item_id}")
                yield {"session_id": thread_id, "project": next(iter(projects)) if len(projects) == 1 else "", "source": "codex"}, events, []
    except (sqlite3.Error, OSError) as exc:
        notice(problems, "codex", f"SQLite 读取失败：{type(exc).__name__}", path)
