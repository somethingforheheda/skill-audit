"""Load, validate and render editable Skill-review rulebooks."""

import json
import sys
import zipfile
from pathlib import Path

import audit_paths


FILES = {
    "single": "单Skill语义审核条例.json",
    "cross": "全量跨Skill语义审核条例.json",
}


class InvalidRulebook(Exception):
    pass


def installed_rules_dir():
    return audit_paths.rules_dir()


def _nonempty(value, where):
    if not isinstance(value, str) or not value.strip():
        raise InvalidRulebook(f"{where} 必须是非空字符串")


def validate(data, kind):
    if not isinstance(data, dict) or set(data) != {"schema_version", "kind", "prefix", "rules", "suffix"}:
        raise InvalidRulebook("顶层字段必须是 schema_version、kind、prefix、rules、suffix")
    if data["schema_version"] != "2.0" or data["kind"] != kind:
        raise InvalidRulebook(f"仅支持 {kind} 条例 schema_version=2.0")
    _nonempty(data["prefix"], "prefix")
    if not isinstance(data["suffix"], str):
        raise InvalidRulebook("suffix 必须是字符串")
    rules = data["rules"]
    if not isinstance(rules, list) or not rules:
        raise InvalidRulebook("rules 必须是非空数组")
    for index, rule in enumerate(rules, 1):
        common = {"id", "title", "criteria_heading", "criteria", "sections"}
        fields = common | ({"level", "allowed_levels"} if kind == "single" else {"severity", "minimum_skills"})
        if kind == "single" and isinstance(rule, dict) and "level_note" in rule:
            fields.add("level_note")
        if not isinstance(rule, dict) or set(rule) != fields:
            raise InvalidRulebook(f"规则 {index} 字段不匹配")
        if type(rule["id"]) is not int or rule["id"] != index:
            raise InvalidRulebook(f"规则编号必须从 1 连续排列；第 {index} 项不符")
        _nonempty(rule["title"], f"规则 {index} title")
        if rule["criteria_heading"] not in ("判定方式", "判定方式与误报边界"):
            raise InvalidRulebook(f"规则 {index} criteria_heading 不受支持")
        _nonempty(rule["criteria"], f"规则 {index} criteria")
        if kind == "single":
            levels = rule["allowed_levels"]
            if (not isinstance(levels, list) or not levels
                    or any(not isinstance(level, str) for level in levels)
                    or len(levels) != len(set(levels))
                    or not set(levels) <= {"BLOCK", "WARN"}):
                raise InvalidRulebook(f"规则 {index} allowed_levels 只能包含 BLOCK/WARN")
            if rule["level"] not in levels:
                raise InvalidRulebook(f"规则 {index} level 与 allowed_levels 不一致")
            if "level_note" in rule:
                _nonempty(rule["level_note"], f"规则 {index} level_note")
        else:
            if rule["severity"] not in ("high", "medium"):
                raise InvalidRulebook(f"规则 {index} severity 只能是 high/medium")
            if type(rule["minimum_skills"]) is not int or rule["minimum_skills"] < 1:
                raise InvalidRulebook(f"规则 {index} minimum_skills 必须是正整数")
        sections = rule["sections"]
        if not isinstance(sections, list) or not sections:
            raise InvalidRulebook(f"规则 {index} sections 必须是非空数组")
        for section_index, section in enumerate(sections, 1):
            where = f"规则 {index} section {section_index}"
            if not isinstance(section, dict):
                raise InvalidRulebook(f"{where} 必须是对象")
            section_type = section.get("type")
            if section_type in ("example", "supplemental_example"):
                keys = {"type", "title", "scenario", "reason", "fix", "not_report"}
            elif section_type == "supplemental_check":
                keys = {"type", "title", "guidance"}
            else:
                raise InvalidRulebook(f"{where} type 不受支持")
            if set(section) != keys:
                raise InvalidRulebook(f"{where} 字段不匹配")
            for key in keys - {"type"}:
                _nonempty(section[key], f"{where} {key}")
    return data


def _parse(raw, kind, location):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidRulebook(f"条例 JSON 存在重复字段：{key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise InvalidRulebook(f"条例 JSON 包含非法常量：{value}")

    try:
        data = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    except (ValueError, TypeError) as exc:
        raise InvalidRulebook(f"条例 JSON 无法解析：{location}：{exc}") from exc
    return validate(data, kind)


def load(kind, override=None):
    if kind not in FILES:
        raise InvalidRulebook(f"未知条例类型：{kind}")
    name = FILES[kind]
    if override is not None:
        path = Path(override).expanduser()
        try:
            return _parse(path.read_text(encoding="utf-8"), kind, path)
        except (OSError, UnicodeError) as exc:
            raise InvalidRulebook(f"无法读取条例：{path}：{exc}") from exc
    direct = Path(__file__).with_name(name)
    installed = installed_rules_dir() / name
    for path in (direct, installed):
        if path.is_file():
            try:
                return _parse(path.read_text(encoding="utf-8"), kind, path)
            except (OSError, UnicodeError) as exc:
                raise InvalidRulebook(f"无法读取条例：{path}：{exc}") from exc
    try:
        with zipfile.ZipFile(sys.argv[0]) as archive:
            return _parse(archive.read(name).decode("utf-8"), kind, sys.argv[0])
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError) as exc:
        raise InvalidRulebook(f"发行包缺少审核条例：{name}") from exc


def render(data):
    output = [data["prefix"]]
    for rule in data["rules"]:
        if data["kind"] == "single":
            level = rule["level"]
            if "level_note" in rule:
                level += f"（{rule['level_note']}）"
        else:
            level = {"high": "高", "medium": "中"}[rule["severity"]]
        parts = [
            f"### {rule['id']}：{rule['title']}",
            f"**级别**：{level}",
            f"**{rule['criteria_heading']}**：{rule['criteria']}",
        ]
        for section in rule["sections"]:
            if section["type"] == "supplemental_check":
                parts.append(f"**补充检查：{section['title']}**\n\n{section['guidance']}")
                continue
            heading = "案例" if section["type"] == "example" else "补充案例"
            parts.append(
                f"**{heading}：{section['title']}**\n\n"
                f"- **问题场景**：{section['scenario']}\n"
                f"- **为什么报**：{section['reason']}\n"
                f"- **建议怎么改**：{section['fix']}\n"
                f"- **什么情况不报**：{section['not_report']}"
            )
        output.append("\n\n".join(parts) + "\n\n")
    output.append(data["suffix"])
    return "".join(output)
