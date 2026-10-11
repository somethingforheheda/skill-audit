#!/usr/bin/env python3
"""Skill 硬性规则检查器（只读，不执行被审脚本，不请求网络）。

依赖：Python 3.9+、PyYAML（python3 -m pip install PyYAML）。
用法：
  python3 skill_lint.py /path/to/skill [/path/to/another-skill] --json
  python3 skill_lint.py --root /path/to/.agents/skills

位置参数是单个 Skill 目录或 SKILL.md；--root 扫描其直接子目录。
未提供目标即报错，避免意外扫描个人目录或参考仓库。软链目录不重复扫描。
退出码：0 无 error；1 发现硬性缺陷；2 参数、依赖或读取错误。
这是目标目录的全量检查，不自动获取 push diff，不安装 Hook；存量问题也会报告。
push 集成需由调用方选定待推送快照，并按差异过滤既有问题后决定拦截。
支持 text / --json 输出；--json 使用固定 JSON 报告，不会回显疑似凭证或文件全文。
JSON 顶层字段为 schema_version、status、checked_skills、uncovered、issues、SKILL_LINT_ISSUES。
status 仅表示检查覆盖情况：complete 全部完成、partial 部分完成、failed 未能完成。
SKILL_LINT_ISSUES 统计确定的硬性问题；它与 AI 的 SKILL_REVIEW_ISSUES 分开。

仅检查：SKILL.md 存在与 UTF-8、YAML 语法/映射、name/description
非空字符串和长度、名称格式及目录一致、全文件物理行数 <500。
YAML 使用 PyYAML SafeLoader 原生语义；不单独检查重复键，允许合法合并覆盖。
其他字段不设白名单，不要求 compatibility/allowed-tools。
路径、资源引用、Markdown、JSON 示例及重复键歧义交语义审核，不遍历附带资源。
passed 只表示上述硬性规则通过，不表示完整 Skill 合格。
读取上限 2 MiB；超限是 unable_to_check，不是 Skill 质量违规。

"""
import argparse
import json
import re
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('skills', nargs='*', help='Skill 目录或 SKILL.md')
    parser.add_argument('--root', type=Path, help='扫描此目录下全部直接子目录 Skill')
    parser.add_argument('--json', action='store_true', help='输出结构化 JSON')
    args = parser.parse_args(argv)
    if not args.skills and args.root is None:
        parser.error('必须指定 Skill 路径或 --root')
    issues = []
    checked = []
    uncovered = []
    coverage = ['仅检查入口、UTF-8、YAML、必填字段、名称与长度、500行上限',
                '路径、引用、Markdown、JSON示例及重复键歧义交语义审核，未在此核验',
                '全量目标检查；未按push diff过滤存量问题；不执行被审脚本']
    def add(rule, path, line, message, fix, level='error'):
        issues.append(dict(rule=rule, level='BLOCK' if level == 'error' else 'WARN',
                           file=str(path), line=line, problem=message, fix=fix))

    def cannot_check(path, reason):
        uncovered.append(dict(path=str(path) if path is not None else None, reason=reason))

    def report():
        blocks = sum(item['level'] == 'BLOCK' for item in issues)
        status = ('complete' if not uncovered else ('partial' if checked else 'failed'))
        return dict(schema_version='1.0', status=status, checked_skills=checked,
                    uncovered=uncovered, issues=issues, SKILL_LINT_ISSUES=blocks)

    try:
        import yaml
    except ImportError:
        cannot_check(None, '缺少 PyYAML；请在所选 Python 环境安装 PyYAML')
        print(json.dumps(report(), ensure_ascii=False, indent=2) if args.json else uncovered[0]['reason'])
        return 2

    targets = [Path(x).absolute() for x in args.skills]
    if args.root is not None:
        root = args.root.absolute()
        if not root.is_dir():
            cannot_check(root, '扫描根目录不存在或非目录')
        else:
            try:
                targets += [p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith('.') and not p.is_symlink()]
            except OSError:
                cannot_check(root, '无法读取扫描根目录')
    seen = set()
    for target in targets:
        folder = target.parent if target.name == 'SKILL.md' else target
        real = folder.resolve(strict=False)
        if real in seen:
            continue
        seen.add(real)
        entry = folder / 'SKILL.md'
        if not folder.is_dir():
            cannot_check(folder, 'Skill 目录不存在')
            continue
        if not entry.is_file():
            checked.append(str(folder))
            add('skill_md_required', entry, 1, '缺少 SKILL.md', '提供 Skill 入口文件')
            continue
        try:
            with entry.open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                cannot_check(entry, '入口超过 2 MiB 读取上限，未完成检查')
                continue
            text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        except (OSError, UnicodeError):
            cannot_check(entry, '无法按 UTF-8 读取')
            continue
        checked.append(str(folder))
        lines = text.split('\n')
        if lines and lines[-1] == '':
            lines.pop()
        if len(lines) >= 500:
            add('skill_md_under_500_lines', entry, 500, f'SKILL.md 共 {len(lines)} 行，要求少于 500 行', '拆分详细资料，正文保留入口和读取条件')
        end = next((i for i in range(1, len(lines)) if lines[i] == '---'), None)
        if not lines or lines[0] != '---' or end is None:
            add('frontmatter_valid', entry, 1, '缺少完整 YAML frontmatter', '文件首行使用 ---，并用独立 --- 行结束 YAML')
        else:
            try:
                data = yaml.safe_load('\n'.join(lines[1:end]))
                if not isinstance(data, dict):
                    add('frontmatter_mapping', entry, 2, 'YAML 必须为映射', '使用 name: 和 description: 等键值字段')
                else:
                    for field, limit in [('name', 64), ('description', 1024)]:
                        value = data.get(field)
                        at = next((i+1 for i in range(1, end) if re.match(r'^'+field+r'\s*:', lines[i])), 2)
                        if not isinstance(value, str) or not value.strip():
                            add('required_field', entry, at, f'{field} 必须为非空字符串', '填写有效字段值')
                        elif len(value) > limit:
                            add('field_length', entry, at, f'{field} 超过 {limit} 字符', '精简字段')
                    name = data.get('name')
                    if isinstance(name, str) and name.strip():
                        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
                            add('name_format', entry, 2, 'name 应为小写字母、数字及单个连字符分隔', '修正 name，不使用首尾或连续连字符')
                        if name != real.name:
                            add('name_directory_match', entry, 2, 'name 与 Skill 目录名不一致', '统一目录名与 name')
            except yaml.YAMLError as exc:
                mark = getattr(exc, 'problem_mark', None)
                add('frontmatter_valid', entry, 2 + (mark.line if mark else 0), 'YAML 语法或安全构造失败', '修正 YAML 语法或不支持的类型标签')
    if not checked and not uncovered:
        cannot_check(args.root if args.root is not None else None, '目标根目录下没有可检查的 Skill，未完成检查')
    result = report()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in issues:
            print(f"{item['level']} {item['file']}:{item['line']} [{item['rule']}] {item['problem']}；{item['fix']}")
        for item in uncovered:
            print(f"UNABLE_TO_CHECK {item['path'] or '环境'}：{item['reason']}")
        print(f"检查 {len(checked)} 个 Skill；block={result['SKILL_LINT_ISSUES']}；status={result['status']}")
        for item in coverage:
            print(f'范围：{item}')
    return 2 if uncovered else (1 if result['SKILL_LINT_ISSUES'] else 0)


if __name__ == '__main__':
    try:
        code = main()
    except Exception as exc:
        # 最外层兜底：解析库/文件系统异常不能冒充通过，也不回显不可信原文。
        reason = f'检查未完成：{type(exc).__name__}，请核实目标或运行环境'
        result = dict(schema_version='1.0', status='failed', checked_skills=[],
                      uncovered=[dict(path=None, reason=reason)], issues=[], SKILL_LINT_ISSUES=0)
        print(json.dumps(result, ensure_ascii=False, indent=2) if '--json' in sys.argv
              else reason)
        code = 2
    sys.exit(code)
