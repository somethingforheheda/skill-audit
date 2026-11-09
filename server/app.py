import json
import mimetypes
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth  # noqa: E402
import icon  # noqa: E402
import browse  # noqa: E402
import config  # noqa: E402
import jobs  # noqa: E402
import page_index  # noqa: E402
import page_settings  # noqa: E402
import page_users  # noqa: E402
import pages  # noqa: E402
import release  # noqa: E402
import runner  # noqa: E402
import store  # noqa: E402

HTML = 'text/html; charset=utf-8'


def public(meta):
    keys = ('id', 'user', 'created', 'status', 'message', 'files', 'bytes', 'limit', 'model', 'subagent_model',
            'effort', 'project', 'days', 'max_episodes', 'all_users')
    return {k: meta.get(k) for k in keys}


def visible(meta, user):
    return not meta.get('all_users') or auth.is_admin(user)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'skill-audit-web'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.log_date_time_string(), fmt % args))

    def send(self, status, body, ctype=HTML, headers=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def send_json(self, status, value):
        self.send(status, json.dumps(value, ensure_ascii=False), 'application/json; charset=utf-8',
                  {'Cache-Control': 'no-store'})

    def redirect(self, where, headers=None):
        self.send(302, b'', HTML, dict({'Location': where}, **(headers or {})))

    def fail(self, status, message):
        self.close_connection = True
        self.send(status, message, 'text/plain; charset=utf-8')

    def me(self):
        return auth.current_user(self.headers)

    def need_api(self):
        if self.headers.get('X-Requested-With') != 'sa':
            self.fail(403, '缺少请求头')
            return None
        user = self.me()
        if not user:
            self.fail(401, '请先登录')
        return user

    def sync_user(self):
        name = (self.headers.get('X-SA-User') or '').strip().lower()
        if not auth.valid_name(name):
            self.fail(401, '缺少或无效的用户 id')
            return None
        return name

    def sync_post(self, path):
        user = self.sync_user()
        if not user:
            return
        raw = self.read_body(8 * 1024 * 1024)
        if raw is None:
            return self.fail(413, '请求过大')
        try:
            data = json.loads(raw.decode('utf-8')) if raw else {}
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return self.fail(400, '请求格式错误')
        try:
            if path == '/api/sync/diff':
                return self.send_json(200, store.diff(user, data.get('files') or []))
            if path == '/api/sync/skills':
                return self.send_json(200, {'saved': store.put_skills(user, data.get('entries') or [])})
        except jobs.JobError as exc:
            return self.fail(exc.status, str(exc))
        self.fail(404, 'not found')

    def sync_put(self, url):
        user = self.sync_user()
        if not user:
            return
        query = parse_qs(url.query)

        def arg(name):
            return (query.get(name) or [''])[0]
        try:
            store.store_file(user, arg('source'), arg('path'), int(arg('mt') or 0), arg('proj'), self.rfile,
                             int(self.headers.get('Content-Length') or 0))
        except jobs.JobError as exc:
            return self.fail(exc.status, str(exc))
        except (ValueError, OSError):
            return self.fail(400, '上传失败')
        self.send_json(200, {'ok': True})

    def read_body(self, limit=65536):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > limit:
            return None
        return self.rfile.read(length)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path == '/healthz':
            return self.send(200, 'ok', 'text/plain')
        if path == '/login':
            return self.send(200, pages.login_page())
        if path in ('/favicon.svg', '/favicon.ico', '/apple-touch-icon.png'):
            cache = {'Cache-Control': 'public, max-age=86400'}
            if path == '/favicon.svg':
                return self.send(200, icon.ICON_SVG, 'image/svg+xml', cache)
            return self.send(200, icon.png_bytes(), 'image/png', cache)
        if path == '/api/sync/version':
            info = release.pyz_info()
            return self.send_json(200 if info else 404, info or {'error': 'no release configured'})
        if path == '/download/skill-audit.pyz' and config.PYZ and os.path.isfile(config.PYZ):
            return self.send(200, open(config.PYZ, 'rb').read(), 'application/octet-stream',
                             {'Content-Disposition': 'attachment; filename="skill-audit"'})
        if path == '/api/sync/status':
            who = self.sync_user()
            return who and self.send_json(200, browse.summary(who, False))
        user = self.me()
        if not user:
            return self.redirect('/login')
        if path == '/settings':
            return self.send(200, page_settings.settings_page(user))
        if path == '/all':
            if not auth.is_admin(user):
                return self.send(403, pages.shell('没有权限', '<div class="card">只有管理员能看全部会话。<a href="/">返回</a></div>', user))
            return self.send(200, page_users.all_page(user))
        if path in ('/api/all', '/api/all/sessions') and not auth.is_admin(user):
            return self.send_json(403, {'error': '只有管理员可以查看'})
        if path == '/api/all':
            return self.send_json(200, browse.summary_all())
        if path == '/api/all/sessions':
            query = parse_qs(url.query)
            project = (query.get('project') or [''])[0] or None
            return self.send_json(200, {'sessions': browse.session_rows(project)[:3000]})
        if path == '/users':
            return self.send(200, page_users.users_page(user, self.headers.get('Host') or 'SERVER:8200'))
        if path.startswith('/u/') and auth.valid_name(path[3:]):
            return self.send(200, page_users.user_page(user, path[3:]))
        if path == '/api/config':
            return self.send_json(200, {'models': config.MODELS, 'default_model': config.MODEL,
                                        'efforts': list(config.EFFORTS), 'max_episodes': config.MAX_EPISODES,
                                        'key_required': runner.key_required()})
        if path == '/api/users':
            return self.send_json(200, {'me': user, 'users': browse.list_users()})
        if path.startswith('/api/users/') and auth.valid_name(path[11:]):
            return self.send_json(200, browse.summary(path[11:]))
        if path == '/':
            return self.send(200, page_index.index_page(user, config.MAX_FILE_MB))
        if path.startswith('/job/'):
            jid = path.split('/')[2]
            if not jobs.get(jid) or not visible(jobs.get(jid), user):
                return self.send(404, pages.shell('不存在', '<div class="card">任务不存在或已删除。<a href="/">返回</a></div>', user))
            return self.send(200, pages.job_page(jid, user))
        if path == '/api/jobs':
            return self.send_json(200, {'me': user, 'jobs': [public(m) for m in jobs.list_jobs() if visible(m, user)]})
        if path.startswith('/api/jobs/') and path.endswith('/owners'):
            meta = jobs.get(path.split('/')[3])
            if not meta or not meta.get('all_users') or not auth.is_admin(user):
                return self.send_json(404, {'error': 'not found'})
            try:
                rows = json.loads((jobs.job_dir(meta['id']) / 'owners.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                rows = []
            return self.send_json(200, {'sessions': rows})
        if path.startswith('/api/jobs/'):
            meta = jobs.get(path.split('/')[3])
            if not meta or not visible(meta, user):
                return self.send_json(404, {'error': 'not found'})
            row = public(meta)
            row.update(mine=meta['user'] == user, log=runner.tail_log(meta['id']) if meta['status'] != 'done' else '')
            return self.send_json(200, row)
        if path.startswith('/r/'):
            return self.report(path, user)
        self.send(404, 'not found', 'text/plain')

    def report(self, path, user):
        parts = path.split('/')[2:]
        jid = parts[0] if parts else ''
        if not jobs.get(jid) or not visible(jobs.get(jid), user):
            return self.send(404, 'not found', 'text/plain')
        base = runner.report_dir(jid).resolve()
        target = (base / '/'.join(parts[1:] or ['report.html'])).resolve()
        if base not in target.parents or not target.is_file():
            return self.send(404, 'not found', 'text/plain')
        ctype = mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/json', 'application/javascript'):
            ctype += '; charset=utf-8'
        self.send(200, target.read_bytes(), ctype, {'Cache-Control': 'no-store'})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/login':
            form = parse_qs((self.read_body() or b'').decode('utf-8', 'replace'))
            name = (form.get('user') or [''])[0].strip().lower()
            if not auth.valid_name(name):
                return self.send(400, pages.login_page('请输入 2-32 位的邮箱前缀（字母、数字、点、下划线、短横线）', name))
            if auth.is_admin(name):
                password = (form.get('password') or [''])[0]
                if not auth.admin_password_ok(password):
                    time.sleep(1)
                    return self.send(401, pages.login_page('管理员密码不对' if password else '这是管理员账号，请输入密码', name, True))
            return self.redirect('/', {'Set-Cookie': auth.login_cookie(name)})
        if path == '/logout':
            return self.redirect('/login', {'Set-Cookie': auth.logout_cookie()})
        if path.startswith('/api/sync/'):
            return self.sync_post(path)
        user = self.need_api()
        if not user:
            return
        try:
            if path == '/api/analyze':
                raw = self.read_body()
                options = json.loads(raw.decode('utf-8')) if raw else {}
                return self.send_json(200, public(runner.create_from_store(user, options if isinstance(options, dict) else {})))
            if path == '/api/jobs':
                raw = self.read_body()
                options = json.loads(raw.decode('utf-8')) if raw else {}
                return self.send_json(200, public(runner.new_job(user, options if isinstance(options, dict) else {})))
            parts = path.split('/')
            if len(parts) == 5 and parts[1] == 'api' and parts[2] == 'jobs':
                if parts[4] == 'start':
                    return self.send_json(200, public(runner.start(parts[3], user)))
                if parts[4] == 'cancel':
                    return self.send_json(200, public(runner.cancel(parts[3], user)))
                if parts[4] == 'delete':
                    runner.delete(parts[3], user)
                    return self.send_json(200, {'ok': True})
        except jobs.JobError as exc:
            return self.fail(exc.status, str(exc))
        except ValueError:
            return self.fail(400, '请求格式错误')
        self.fail(404, 'not found')

    def do_PUT(self):
        url = urlparse(self.path)
        parts = url.path.split('/')
        if url.path == '/api/sync/file':
            return self.sync_put(url)
        user = self.need_api()
        if not user:
            return
        if not (len(parts) == 5 and parts[1] == 'api' and parts[2] == 'jobs' and parts[4] == 'files'):
            return self.fail(404, 'not found')
        query = parse_qs(url.query)
        try:
            length = int(self.headers.get('Content-Length') or 0)
            jobs.save_file(parts[3], user, (query.get('source') or [''])[0], (query.get('path') or [''])[0],
                           self.rfile, length)
        except jobs.JobError as exc:
            return self.fail(exc.status, str(exc))
        except (ValueError, OSError):
            return self.fail(400, '上传失败')
        self.send_json(200, {'ok': True})


def main():
    config.ensure_dirs()
    runner.start_worker()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    server.daemon_threads = True
    sys.stderr.write('listening on %s:%d, data %s\n' % (config.HOST, config.PORT, config.DATA))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
