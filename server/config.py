import os
import shlex
from pathlib import Path

DATA = Path(os.environ.get('SA_DATA', './data')).expanduser().resolve()
HOST = os.environ.get('SA_HOST', '0.0.0.0')
PORT = int(os.environ.get('SA_PORT', '8200'))
AUDIT_CMD = shlex.split(os.environ.get('SA_AUDIT_CMD', 'skill-audit'))
SKILLS_ROOT = os.environ.get('SA_SKILLS_ROOT', '')
PYZ = os.environ.get('SA_PYZ', '')
BACKEND = os.environ.get('SA_BACKEND', 'claude-code')
MODEL = os.environ.get('SA_MODEL', '')
SUBAGENT_MODEL = os.environ.get('SA_SUBAGENT_MODEL', '')
MODELS = [m.strip() for m in os.environ.get('SA_MODELS', '').split(',') if m.strip()]
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
ENV_FILE = os.environ.get('SA_ENV_FILE', '')
USER_KEY = os.environ.get('SA_USER_KEY', 'optional')
KEY_VAR = os.environ.get('SA_KEY_VAR', 'ANTHROPIC_AUTH_TOKEN')
EXTRA_PATH = os.environ.get('SA_EXTRA_PATH', '')
WORKERS = int(os.environ.get('SA_WORKERS', '1'))
MAX_EPISODES = int(os.environ.get('SA_MAX_EPISODES', '30'))
JOB_TIMEOUT = int(os.environ.get('SA_JOB_TIMEOUT', '5400'))
MAX_FILE_MB = int(os.environ.get('SA_MAX_FILE_MB', '200'))
MAX_JOB_MB = int(os.environ.get('SA_MAX_JOB_MB', '1500'))
MIN_FREE_GB = int(os.environ.get('SA_MIN_FREE_GB', '5'))
USER_QUOTA_GB = int(os.environ.get('SA_USER_QUOTA_GB', '10'))
KEEP_UPLOADS = os.environ.get('SA_KEEP_UPLOADS', '') == '1'
SECURE_COOKIE = os.environ.get('SA_SECURE_COOKIE', '') == '1'

JOBS = DATA / 'jobs'
RUNNER_HOME = DATA / 'runner-home'

SOURCES = {
    'claude': ('.claude/projects', lambda name: name.endswith('.jsonl')),
    'codex': ('.codex/sessions', lambda name: name.endswith('.jsonl')),
    'comate': ('.comate-engine/store', lambda name: name.startswith('chat_session_') and not name.endswith('.deleted')),
}


def ensure_dirs():
    JOBS.mkdir(parents=True, exist_ok=True)
    RUNNER_HOME.mkdir(parents=True, exist_ok=True)
