#!/usr/bin/env python3
"""Build an offline zipapp and a team-shareable ZIP using only Python stdlib."""

import shutil
import tempfile
import zipapp
import zipfile
from pathlib import Path

import rulebook


SOURCE = Path(__file__).resolve().parent
DIST = SOURCE / "dist"
APP_FILES = (
    "__main__.py",
    "skill_audit_cli.py",
    "skill_lint.py",
    "review_output_check.py",
    "rulebook.py",
    "audit_paths.py",
    "history_review.py",
    "history_pipeline.py",
    "history_reader.py",
    "history_extract.py",
    "history_analysis.py",
    "skill_scope.py",
    "history_export.py",
    "history_report.html",
    "codex_process.py",
    "audit_config.py",
    "单Skill语义审核条例.json",
    "全量跨Skill语义审核条例.json",
)


def main():
    for kind, name in rulebook.FILES.items():
        rulebook.load(kind, SOURCE / name)
    DIST.mkdir(exist_ok=True)
    app = DIST / "skill-audit.pyz"
    bundle = DIST / "skill-audit-team.zip"
    with tempfile.TemporaryDirectory(prefix="skill-audit-build-") as directory:
        stage = Path(directory)
        for name in APP_FILES:
            shutil.copy2(SOURCE / name, stage / name)
        # Backends are discovered by module, so a new adapter needs no edit here.
        (stage / "agent_backends").mkdir()
        for module in sorted((SOURCE / "agent_backends").glob("*.py")):
            shutil.copy2(module, stage / "agent_backends" / module.name)
        zipapp.create_archive(stage, app, interpreter="/usr/bin/env python3", compressed=True)
    app.chmod(0o755)
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.write(app, "skill-audit.pyz")
        for name in ("README.md", "requirements.txt", "install.sh"):
            output.write(SOURCE / name, name)
        for name in rulebook.FILES.values():
            output.write(SOURCE / name, name)
    print(f"已生成 {app}\n已生成 {bundle}")


if __name__ == "__main__":
    main()
