#!/usr/bin/env python3
"""Validate single-Skill or cross-Skill AI review output without executing it."""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath

import rulebook


SINGLE_KEYS = {"schema_version", "skill", "status", "uncovered", "issues", "SKILL_REVIEW_ISSUES"}
SINGLE_ISSUE_KEYS = {
    "rule", "level", "confidence", "file", "line", "quote", "problem",
    "fix", "client", "client_basis",
}
CROSS_KEYS = {
    "schema_version", "run_id", "repository", "commit", "started_at",
    "finished_at", "status", "expected_skills", "scanned_skills", "uncovered",
    "findings",
}
CROSS_FINDING_KEYS = {
    "rule", "severity", "confidence", "skills", "evidence", "condition",
    "problem", "impact", "fix",
}


class InvalidReport(Exception):
    pass


def fail(where, message):
    raise InvalidReport(f"{where}: {message}")


def exact_keys(value, keys, where):
    if not isinstance(value, dict):
        fail(where, "必须是 JSON 对象")
    missing, extra = keys - value.keys(), value.keys() - keys
    if missing or extra:
        fail(where, f"字段不匹配，缺少 {sorted(missing)}，多余 {sorted(extra)}")


def nonempty_string(value, where):
    if not isinstance(value, str) or not value.strip():
        fail(where, "必须是非空字符串")


def integer(value, where, minimum=0):
    if type(value) is not int or value < minimum:
        fail(where, f"必须是大于等于 {minimum} 的整数")


def confidence(value, where):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        fail(where, "必须是 0 到 1 的有限数值")


def choice(value, options, where):
    if not isinstance(value, str) or value not in options:
        fail(where, f"必须是 {sorted(options)} 之一")


def repo_path(value, root, where, must_exist=True):
    nonempty_string(value, where)
    posix = PurePosixPath(value)
    if (value.startswith("/") or "\\" in value or posix.is_absolute()
            or any(part in (".", "..") for part in value.split("/"))):
        fail(where, "必须是无 . 或 .. 的仓库相对路径")
    path = root.joinpath(*posix.parts)
    if must_exist and (not path.is_file() or not path.resolve().is_relative_to(root.resolve())):
        fail(where, "文件不存在或指向仓库之外")
    return path


def evidence(value, root, where):
    exact_keys(value, {"file", "line", "quote"}, where)
    path = repo_path(value["file"], root, where + ".file")
    integer(value["line"], where + ".line", 1)
    nonempty_string(value["quote"], where + ".quote")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        fail(where, f"无法读取引用文件：{exc}")
    if value["line"] > len(lines) or value["quote"] not in lines[value["line"] - 1]:
        fail(where, "quote 与指定行原文不符")


def uncovered(value, root, status):
    if not isinstance(value, list):
        fail("uncovered", "必须是数组")
    for index, item in enumerate(value):
        where = f"uncovered[{index}]"
        exact_keys(item, {"path", "reason"}, where)
        repo_path(item["path"], root, where + ".path", must_exist=False)
        nonempty_string(item["reason"], where + ".reason")
    if (status == "complete") != (len(value) == 0):
        fail("uncovered", "complete 时必须为空；partial/failed 时必须说明未覆盖原因")


def validate_single(data, root, policy=None):
    policy = policy or rulebook.load("single")
    rules = {item["id"]: item for item in policy["rules"]}
    exact_keys(data, SINGLE_KEYS, "报告")
    if data["schema_version"] != "1.0":
        fail("schema_version", "当前仅支持 1.0")
    repo_path(data["skill"], root, "skill")
    if PurePosixPath(data["skill"]).name != "SKILL.md":
        fail("skill", "必须指向 SKILL.md")
    choice(data["status"], {"complete", "partial", "failed"}, "status")
    uncovered(data["uncovered"], root, data["status"])
    if not isinstance(data["issues"], list):
        fail("issues", "必须是数组")
    blocks = 0
    for index, item in enumerate(data["issues"]):
        where = f"issues[{index}]"
        exact_keys(item, SINGLE_ISSUE_KEYS, where)
        integer(item["rule"], where + ".rule", 1)
        if item["rule"] not in rules:
            fail(where + ".rule", "条例中不存在该规则编号")
        choice(item["level"], {"BLOCK", "WARN"}, where + ".level")
        if item["level"] not in rules[item["rule"]]["allowed_levels"]:
            fail(where + ".level", "该规则不允许此级别")
        confidence(item["confidence"], where + ".confidence")
        for key in ("problem", "fix"):
            nonempty_string(item[key], where + "." + key)
        choice(item["client"], {"both", "claude_code", "codex"}, where + ".client")
        if item["client"] == "both":
            if item["client_basis"] is not None:
                fail(where + ".client_basis", "client=both 时须为 null")
        else:
            nonempty_string(item["client_basis"], where + ".client_basis")
        evidence({key: item[key] for key in ("file", "line", "quote")}, root, where)
        blocks += item["level"] == "BLOCK"
    integer(data["SKILL_REVIEW_ISSUES"], "SKILL_REVIEW_ISSUES")
    if blocks != data["SKILL_REVIEW_ISSUES"]:
        fail("SKILL_REVIEW_ISSUES", f"应为 {blocks}，实际为 {data['SKILL_REVIEW_ISSUES']}")


def timestamp(value, where):
    nonempty_string(value, where)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        fail(where, "必须是 ISO 8601 时间")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail(where, "必须包含时区")
    return parsed


def validate_cross(data, root, policy=None):
    policy = policy or rulebook.load("cross")
    rules = {item["id"]: item for item in policy["rules"]}
    exact_keys(data, CROSS_KEYS, "报告")
    if data["schema_version"] != "1.0":
        fail("schema_version", "当前仅支持 1.0")
    for key in ("run_id", "repository", "commit"):
        nonempty_string(data[key], key)
    start = timestamp(data["started_at"], "started_at")
    end = timestamp(data["finished_at"], "finished_at")
    if end < start:
        fail("finished_at", "不能早于 started_at")
    choice(data["status"], {"complete", "partial", "failed"}, "status")
    integer(data["expected_skills"], "expected_skills")
    integer(data["scanned_skills"], "scanned_skills")
    if data["scanned_skills"] > data["expected_skills"]:
        fail("scanned_skills", "不能大于 expected_skills")
    uncovered(data["uncovered"], root, data["status"])
    if data["status"] == "complete" and data["scanned_skills"] != data["expected_skills"]:
        fail("status", "complete 时扫描数必须等于预期数")
    if not isinstance(data["findings"], list):
        fail("findings", "必须是数组")
    for index, item in enumerate(data["findings"]):
        where = f"findings[{index}]"
        exact_keys(item, CROSS_FINDING_KEYS, where)
        integer(item["rule"], where + ".rule", 1)
        if item["rule"] not in rules:
            fail(where + ".rule", "条例中不存在该规则编号")
        choice(item["severity"], {"high", "medium"}, where + ".severity")
        expected_severity = rules[item["rule"]]["severity"]
        if item["severity"] != expected_severity:
            fail(where + ".severity", f"规则 {item['rule']} 应为 {expected_severity}")
        confidence(item["confidence"], where + ".confidence")
        for key in ("condition", "problem", "impact", "fix"):
            nonempty_string(item[key], where + "." + key)
        skills = item["skills"]
        minimum_skills = rules[item["rule"]]["minimum_skills"]
        if not isinstance(skills, list) or len(skills) < minimum_skills:
            fail(where + ".skills", f"规则 {item['rule']} 至少涉及 {minimum_skills} 个 Skill")
        for skill in skills:
            repo_path(skill, root, where + ".skills")
            if PurePosixPath(skill).name != "SKILL.md":
                fail(where + ".skills", "必须指向 SKILL.md")
        if len(set(skills)) != len(skills):
            fail(where + ".skills", "不能重复")
        items = item["evidence"]
        if not isinstance(items, list) or len(items) < len(skills):
            fail(where + ".evidence", "每个涉及的 Skill 至少需要一处原文")
        for n, entry in enumerate(items):
            evidence(entry, root, f"{where}.evidence[{n}]")
        if not all(
            any(
                entry["file"] == skill
                or entry["file"].startswith(str(PurePosixPath(skill).parent) + "/")
                for entry in items
            )
            for skill in skills
        ):
            fail(where + ".evidence", "必须包含每个涉及 Skill 的原文")


def read_report(path):
    text = path.read_text(encoding="utf-8")
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("JSON", f"重复字段 {key}")
            result[key] = value
        return result

    try:
        data = json.loads(
            text,
            object_pairs_hook=unique_keys,
            parse_constant=lambda value: fail("JSON", f"非法常量 {value}"),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        fail("JSON", str(exc))
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("single", "cross"))
    parser.add_argument("report", type=Path, help="AI 输出保存的 UTF-8 文件")
    parser.add_argument("--repo-root", type=Path, required=True, help="被审核的仓库快照根目录")
    parser.add_argument("--rules-file", type=Path, help="临时使用指定的条例 JSON")
    args = parser.parse_args(argv)
    try:
        if not args.repo_root.is_dir():
            fail("repo-root", "目录不存在")
        policy = rulebook.load(args.kind, args.rules_file)
        data = read_report(args.report)
        if args.kind == "single":
            validate_single(data, args.repo_root, policy)
        else:
            validate_cross(data, args.repo_root, policy)
    except (OSError, UnicodeError, InvalidReport, rulebook.InvalidRulebook) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print("VALID: 输出结构、计数和原文定位通过；语义判断仍需审核")
    return 0


if __name__ == "__main__":
    sys.exit(main())
