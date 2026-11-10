import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix='sa-web-')
os.environ.update(SA_DATA=TMP, SA_AUDIT_CMD='%s %s' % (sys.executable, os.path.join(HERE, 'stub_audit.py')),
                  SA_MIN_FREE_GB='0', SA_MAX_FILE_MB='1', SA_JOB_TIMEOUT='60', SA_MODEL='default-model')
sys.path.insert(0, os.path.dirname(HERE))

import app  # noqa: E402
import config  # noqa: E402
import jobs  # noqa: E402
import runner  # noqa: E402

H = {'X-Requested-With': 'sa'}


def auth_sign_plain(name):
    import hashlib
    import hmac
    import auth
    return hmac.new(auth._secret(), name.encode(), hashlib.sha256).hexdigest()[:40]


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.ensure_dirs()
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.server.daemon_threads = True
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        runner.start_worker()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    def call(self, method, path, body=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        head = dict(headers or {})
        if cookie:
            head['Cookie'] = cookie
        conn.request(method, path, body=body, headers=head)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def login(self, name='alice'):
        res, _ = self.call('POST', '/login', 'user=%s' % name, headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(res.status, 302)
        return res.getheader('Set-Cookie').split(';')[0]

    def admin_cookie(self, name):
        import auth
        return 'sa_user=%s.%s' % (name, auth._sign(name))

    def api(self, method, path, cookie, body=None):
        res, data = self.call(method, path, body, cookie, H)
        return res.status, data

    def test_login_required_and_validation(self):
        res, _ = self.call('GET', '/')
        self.assertEqual((res.status, res.getheader('Location')), (302, '/login'))
        res, _ = self.call('POST', '/login', 'user=../x', headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(res.status, 400)
        res, _ = self.call('GET', '/', cookie='sa_user=alice.' + 'f' * 40)
        self.assertEqual(res.status, 302)
        res, data = self.call('GET', '/', cookie=self.login())
        self.assertEqual(res.status, 200)
        self.assertIn('分析任务'.encode(), data)

    def test_admin_needs_a_password_and_alone_sees_and_analyzes_everyones_sessions(self):
        from unittest import mock
        import store
        form = {'Content-Type': 'application/x-www-form-urlencoded'}
        for user in ('annx', 'bobx'):
            folder = store.user_dir(user) / 'sessions' / 'claude' / '-w-app'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ('%s.jsonl' % user)).write_text('{}\n', encoding='utf-8')
            store.append(user, {'k': 'claude/-w-app/%s.jsonl' % user, 's': 3, 'm': int(time.time()), 'p': '/w/team-app'})
        key_file = config.DATA / 'admin.key'
        with mock.patch.object(config, 'ADMINS', ['boss']):
            res, data = self.call('POST', '/login', 'user=boss', headers=form)
            self.assertEqual(res.status, 401)
            self.assertIn('管理员密码'.encode(), data)
            res, _ = self.call('POST', '/login', 'user=boss&password=s3cret-pass', headers=form)
            self.assertEqual(res.status, 401)
            key_file.write_text('s3cret-pass\n', encoding='utf-8')
            try:
                res, _ = self.call('POST', '/login', 'user=boss&password=wrong', headers=form)
                self.assertEqual(res.status, 401)
                res, _ = self.call('POST', '/login', 'user=boss&password=s3cret-pass', headers=form)
                self.assertEqual(res.status, 302)
                boss = res.getheader('Set-Cookie').split(';')[0]
                plain = 'sa_user=boss.' + auth_sign_plain('boss')
                self.assertEqual(self.call('GET', '/all', cookie=plain)[0].status, 302)
                alice = self.login('alicex')
                self.assertEqual(self.call('GET', '/all', cookie=alice)[0].status, 403)
                self.assertEqual(self.api('GET', '/api/all', alice)[0], 403)
                self.assertEqual(self.api('GET', '/api/all/sessions', alice)[0], 403)
                res, data = self.call('GET', '/all', cookie=boss)
                self.assertEqual(res.status, 200)
                status, data = self.api('GET', '/api/all', boss)
                row = [p for p in json.loads(data)['projects'] if p['name'] == 'team-app'][0]
                self.assertEqual(row['owners'], {'annx': 1, 'bobx': 1})
                status, data = self.api('GET', '/api/all/sessions?project=team-app', boss)
                self.assertEqual(sorted(r['user'] for r in json.loads(data)['sessions']), ['annx', 'bobx'])
                body = json.dumps({'all_users': True, 'project': 'team-app'})
                self.assertEqual(self.api('POST', '/api/analyze', alice, body)[0], 403)
                status, data = self.api('POST', '/api/analyze', boss, body)
                self.assertEqual(status, 200, data)
                jid = json.loads(data)['id']
                self.assertEqual(json.loads(data)['files'], 2)
                status, data = self.api('GET', '/api/jobs/%s/owners' % jid, boss)
                self.assertEqual(sorted(r['user'] for r in json.loads(data)['sessions']), ['annx', 'bobx'])
                self.assertEqual(self.api('GET', '/api/jobs/%s/owners' % jid, alice)[0], 404)
                self.assertEqual(self.api('GET', '/api/jobs/%s' % jid, alice)[0], 404)
                self.assertEqual(self.call('GET', '/job/%s' % jid, cookie=alice)[0].status, 404)
                self.assertEqual(self.call('GET', '/r/%s/report.html' % jid, cookie=alice)[0].status, 404)
                listed = [j['id'] for j in json.loads(self.api('GET', '/api/jobs', alice)[1])['jobs']]
                self.assertNotIn(jid, listed)
                self.assertIn(jid, [j['id'] for j in json.loads(self.api('GET', '/api/jobs', boss)[1])['jobs']])
                for _ in range(100):
                    if json.loads(self.api('GET', '/api/jobs/%s' % jid, boss)[1])['status'] in ('done', 'failed'):
                        break
                    time.sleep(0.2)
            finally:
                key_file.unlink()

    def test_manual_upload_form_has_workspace_filter_and_duplicate_confirmation(self):
        import shutil as sh
        import subprocess
        import tempfile
        res, data = self.call('GET', '/', cookie=self.login())
        text = data.decode()
        for needle in ('id="ws-btn"', 'id="ws-list"', 'id="cf-dup"', 'id="cf-ok"', 'id="cf-back"'):
            self.assertIn(needle, text)
        self.assertIn('id="go" disabled type="button" hidden', text)
        self.assertIn('id="go-up"', text)
        self.assertIn('不需要填 API Key', text)
        from unittest import mock
        with mock.patch.object(config, 'ADMINS', ['alice']):
            res, data = self.call('GET', '/', cookie=self.admin_cookie('alice'))
        self.assertNotIn('id="go" disabled type="button" hidden', data.decode())
        if sh.which('node'):
            script = text.split('<script>')[1].split('</script>')[0]
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as handle:
                handle.write('new Function(%s)' % json.dumps(script))
            try:
                done = subprocess.run(['node', handle.name], capture_output=True, text=True)
            finally:
                os.unlink(handle.name)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_api_needs_header_and_login(self):
        cookie = self.login()
        res, _ = self.call('POST', '/api/jobs', '{}', cookie)
        self.assertEqual(res.status, 403)
        res, _ = self.call('POST', '/api/jobs', '{}', None, H)
        self.assertEqual(res.status, 401)

    def make_job(self, cookie):
        status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'limit': 5}))
        self.assertEqual(status, 200)
        return json.loads(data)['id']

    def put(self, cookie, jid, rel, body=b'{"a":1}\n', source='claude'):
        from urllib.parse import quote
        return self.api('PUT', '/api/jobs/%s/files?source=%s&path=%s' % (jid, source, quote(rel)), cookie, body)

    def wait(self, cookie, jid, final=('done', 'failed')):
        for _ in range(100):
            _, data = self.api('GET', '/api/jobs/' + jid, cookie)
            row = json.loads(data)
            if row['status'] in final:
                return row
            time.sleep(0.2)
        self.fail('job did not finish: %s' % row)

    def test_settings_page_holds_the_key_and_model_fields_once(self):
        res, _ = self.call('GET', '/settings')
        self.assertEqual(res.status, 302)
        cookie = self.login()
        res, data = self.call('GET', '/settings', cookie=cookie)
        self.assertEqual(res.status, 200)
        self.assertIn(b'id="o-key"', data)
        for path in ('/', '/u/alice'):
            _, page = self.call('GET', path, cookie=cookie)
            self.assertNotIn(b'id="o-key"', page, path)
            self.assertIn('href="/settings"'.encode(), page, path)

    def test_scope_is_either_days_or_session_count(self):
        cookie = self.login()
        cases = (({'days': 30}, config.MAX_SESSIONS), ({'limit': 7}, 7), ({'limit': 7, 'days': 3}, config.MAX_SESSIONS),
                 ({}, 20), ({'limit': 99999}, config.MAX_SESSIONS))
        for body, want in cases:
            status, data = self.api('POST', '/api/jobs', cookie, json.dumps(body))
            row = json.loads(data)
            self.assertEqual((status, row['limit'], row['max_episodes']), (200, want, config.MAX_EPISODES), body)

    def test_job_page_embeds_the_finished_report(self):
        cookie = self.login()
        jid = self.make_job(cookie)
        self.put(cookie, jid, 'p/s.jsonl')
        self.api('POST', '/api/jobs/%s/start' % jid, cookie)
        self.assertEqual(self.wait(cookie, jid)['status'], 'done')
        _, page = self.call('GET', '/job/' + jid, cookie=cookie)
        self.assertIn(b'id="reportframe"', page)
        self.assertIn(('/r/%s/report.html' % jid).encode(), page)

    def test_failure_message_explains_known_causes_and_drops_repeats(self):
        self.assertIn('登录', runner.explain('x\nPlease check login status, try login again with: ducc login'))
        self.assertIn('API Key', runner.explain('{"error":{"message":"无效的令牌"}}'))
        self.assertEqual(runner.explain('something else'), '')
        jid = self.make_job(self.login())
        with open(os.path.join(config.JOBS, jid, 'run.log'), 'w', encoding='utf-8') as handle:
            handle.write('a\nsame\nsame\nsame\nb\n')
        self.assertEqual(runner.short_tail(jid), 'a | same | b')

    def test_a_bad_key_or_model_is_refused_up_front_by_a_quick_gateway_check(self):
        import http.server
        from unittest import mock
        seen = []

        class Gateway(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append((self.path, self.headers.get('Authorization'), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                token = self.headers.get('Authorization', '')
                if token != 'Bearer good-key-0123':
                    code, body = 401, {'error': {'message': '无效的令牌 (request id: 1)'}}
                elif seen[-1][2]['model'] != 'Good Model':
                    code, body = 503, {'error': {'message': 'model not found'}}
                else:
                    code, body = 200, {'id': 'x'}
                data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        gateway = http.server.HTTPServer(('127.0.0.1', 0), Gateway)
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        env_file = os.path.join(TMP, 'gateway.env')
        with open(env_file, 'w', encoding='utf-8') as handle:
            handle.write('ANTHROPIC_BASE_URL=http://127.0.0.1:%d\n' % gateway.server_address[1])
        cookie = self.login()
        try:
            with mock.patch.object(config, 'ENV_FILE', env_file):
                status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': 'bad-key-0123456', 'model': 'Good Model'}))
                self.assertEqual(status, 400)
                self.assertIn('API Key', data.decode())
                self.assertNotIn('request id', data.decode())
                status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': 'good-key-0123', 'model': 'Nope'}))
                self.assertEqual(status, 400)
                self.assertIn('Nope', data.decode())
                status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': 'good-key-0123', 'model': 'Good Model[1M]'}))
                self.assertEqual(status, 200)
        finally:
            gateway.shutdown()
        self.assertEqual(seen[-1][0], '/v1/messages')
        self.assertEqual(seen[-1][2]['model'], 'Good Model')

    def test_a_gateway_that_connects_but_never_answers_gets_an_accurate_message(self):
        import http.server
        from unittest import mock

        class Slow(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                time.sleep(1.5)

        gateway = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Slow)
        gateway.daemon_threads = True
        threading.Thread(target=gateway.serve_forever, daemon=True).start()
        env_file = os.path.join(TMP, 'slow.env')
        with open(env_file, 'w', encoding='utf-8') as handle:
            handle.write('ANTHROPIC_BASE_URL=http://127.0.0.1:%d\n' % gateway.server_address[1])
        cookie = self.login()
        try:
            with mock.patch.object(config, 'ENV_FILE', env_file), mock.patch.object(runner, 'PREFLIGHT_SECONDS', 0.3):
                status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': 'good-key-0123', 'model': 'Slow Model'}))
        finally:
            gateway.shutdown()
        self.assertEqual(status, 502)
        self.assertIn('没有回应', data.decode())
        self.assertIn('Slow Model', data.decode())
        self.assertNotIn('连不上', data.decode())

    def test_the_key_is_never_sent_to_a_gateway_outside_the_allowed_list(self):
        from unittest import mock
        cookie = self.login()
        env_file = os.path.join(TMP, 'allow.env')
        for url, host in (('https://evil.example.com', 'evil.example.com'), ('http://gw.example.com', 'gw.example.com'), ('', '')):
            with open(env_file, 'w', encoding='utf-8') as handle:
                handle.write('ANTHROPIC_BASE_URL=%s\n' % url if url else '\n')
            with mock.patch.object(config, 'ENV_FILE', env_file), mock.patch.object(config, 'GATEWAY_HOSTS', ['gw.example.com']):
                status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': 'some-key-0123456'}))
            self.assertEqual(status, 503, url)
            self.assertIn('允许列表', data.decode())
        home = runner.build_env('k')
        self.assertTrue(home['SKILL_AUDIT_HOME'].endswith('.skill-audit'))
        self.assertEqual(home['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'], '1')
        with mock.patch.object(config, 'GATEWAY_HOSTS', ['gw.example.com']):
            runner.build_env('k')
        with open(os.path.join(home['SKILL_AUDIT_HOME'], 'config.json'), encoding='utf-8') as handle:
            self.assertEqual(json.load(handle), {'trusted_gateways': ['gw.example.com']})

    def test_icon_is_served_without_login_and_linked_from_pages(self):
        res, data = self.call('GET', '/favicon.svg')
        self.assertEqual((res.status, res.getheader('Content-Type')), (200, 'image/svg+xml'))
        self.assertIn(b'<svg', data)
        for path in ('/favicon.ico', '/apple-touch-icon.png'):
            res, data = self.call('GET', path)
            self.assertEqual((res.status, res.getheader('Content-Type'), data[:4]), (200, 'image/png', b'\x89PNG'), path)
        _, page = self.call('GET', '/login')
        self.assertIn(b'rel="icon"', page)

    def test_upload_rules(self):
        cookie = self.login()
        jid = self.make_job(cookie)
        self.assertEqual(self.put(cookie, jid, 'proj/a.jsonl')[0], 200)
        for bad in ('../a.jsonl', 'proj/a.txt', '/abs/a.jsonl', 'a//b.jsonl', 'a\\b.jsonl'):
            self.assertEqual(self.put(cookie, jid, bad)[0], 400, bad)
        try:
            self.assertEqual(self.put(cookie, jid, 'proj/big.jsonl', b'x' * (1024 * 1024 + 1))[0], 413)
        except ConnectionError:
            pass
        self.assertEqual(self.put(cookie, jid, 'proj/b.jsonl', source='nope')[0], 400)
        self.assertEqual(self.put(self.login('bob'), jid, 'proj/c.jsonl')[0], 403)
        saved = os.path.join(config.JOBS, jid, 'home', '.claude', 'projects', 'proj', 'a.jsonl')
        self.assertTrue(os.path.isfile(saved))
        self.assertEqual(jobs.get(jid)['files'], 1)

    def test_full_flow_and_cleanup(self):
        cookie = self.login()
        jid = self.make_job(cookie)
        status, _ = self.api('POST', '/api/jobs/%s/start' % jid, cookie)
        self.assertEqual(status, 400)
        self.put(cookie, jid, 'p1/s1.jsonl')
        self.put(cookie, jid, 'chat_session_1', source='comate')
        self.assertEqual(self.api('POST', '/api/jobs/%s/start' % jid, cookie)[0], 200)
        self.assertEqual(self.put(cookie, jid, 'p1/s2.jsonl')[0], 409)
        row = self.wait(cookie, jid)
        self.assertEqual(row['status'], 'done', row)
        res, data = self.call('GET', '/r/%s/report.html' % jid, cookie=cookie)
        self.assertEqual(res.status, 200)
        text = data.decode()
        self.assertIn('stub report: 1 files', text)
        self.assertIn('--sources', text)
        self.assertIn(str(config.RUNNER_HOME), text)
        self.assertTrue(os.path.exists(os.path.join(config.JOBS, jid, 'home')))
        res, _ = self.call('GET', '/r/%s/../job.json' % jid, cookie=cookie)
        self.assertEqual(res.status, 404)
        res, _ = self.call('GET', '/r/%s/report.html' % jid)
        self.assertEqual(res.status, 302)
        self.assertEqual(self.api('POST', '/api/jobs/%s/delete' % jid, self.login('bob'))[0], 403)
        self.assertEqual(self.api('POST', '/api/jobs/%s/delete' % jid, cookie)[0], 200)
        self.assertIsNone(jobs.get(jid))

    def test_model_and_effort_are_validated_and_passed_to_the_command(self):
        cookie = self.login()
        for bad in ({'model': '--evil'}, {'model': '-x'}, {'model': 'a\nb'}, {'subagent_model': 'x;y'}, {'effort': 'extreme'}):
            self.assertEqual(self.api('POST', '/api/jobs', cookie, json.dumps(bad))[0], 400, bad)
        status, data = self.api('POST', '/api/jobs', cookie, json.dumps(
            {'model': 'my-model[1m]', 'subagent_model': 'sub-model', 'effort': 'high'}))
        self.assertEqual(status, 200)
        jid = json.loads(data)['id']
        self.put(cookie, jid, 'p/s.jsonl')
        self.assertEqual(self.api('POST', '/api/jobs/%s/start' % jid, cookie)[0], 200)
        self.assertEqual(self.wait(cookie, jid)['status'], 'done')
        text = self.call('GET', '/r/%s/report.html' % jid, cookie=cookie)[1].decode()
        for part in ('--model', 'my-model[1m]', '--subagent-model', 'sub-model', '--effort', 'high'):
            self.assertIn(part, text)
        status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'model': 'Opus 5.5[1M]', 'subagent_model': 'Claude Sonnet 5.5[1M]'}))
        self.assertEqual(status, 200)
        jid = json.loads(data)['id']
        self.put(cookie, jid, 'p/s.jsonl')
        self.api('POST', '/api/jobs/%s/start' % jid, cookie)
        self.wait(cookie, jid)
        text = self.call('GET', '/r/%s/report.html' % jid, cookie=cookie)[1].decode()
        self.assertIn('"Opus 5.5[1M]"', text)
        self.assertIn('"Claude Sonnet 5.5[1M]"', text)
        jid = self.make_job(cookie)
        self.put(cookie, jid, 'p/s.jsonl')
        self.api('POST', '/api/jobs/%s/start' % jid, cookie)
        self.wait(cookie, jid)
        text = self.call('GET', '/r/%s/report.html' % jid, cookie=cookie)[1].decode()
        self.assertIn('default-model', text)
        self.assertNotIn('--effort', text)

    def test_a_model_is_required_when_the_server_has_no_default(self):
        from unittest import mock
        cookie = self.login()
        jid = self.make_job(cookie)
        self.put(cookie, jid, 'p/s.jsonl')
        with mock.patch.object(config, 'MODEL', ''):
            status, data = self.api('POST', '/api/jobs/%s/start' % jid, cookie)
        self.assertEqual(status, 400)
        self.assertIn('模型', data.decode())

    def test_user_key_reaches_the_process_but_is_never_stored(self):
        from unittest import mock
        cookie = self.login()
        secret = 'sk-test-' + 'k' * 20
        with mock.patch.object(config, 'USER_KEY', 'required'):
            self.assertEqual(json.loads(self.api('GET', '/api/config', cookie)[1])['key_required'], True)
            self.assertEqual(self.api('POST', '/api/jobs', cookie, json.dumps({}))[0], 400)
            self.assertEqual(self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': 'bad key 1'}))[0], 400)
            status, data = self.api('POST', '/api/jobs', cookie, json.dumps({'api_key': secret}))
            self.assertEqual(status, 200)
            self.assertNotIn(secret, data.decode())
            jid = json.loads(data)['id']
            self.put(cookie, jid, 'p/s.jsonl')
            self.assertEqual(self.api('POST', '/api/jobs/%s/start' % jid, cookie)[0], 200)
            self.assertEqual(self.wait(cookie, jid)['status'], 'done')
        text = self.call('GET', '/r/%s/report.html' % jid, cookie=cookie)[1].decode()
        self.assertIn('TOKEN=' + secret, text)
        self.assertNotIn(jid, runner.KEYS)
        for root, _, names in os.walk(str(config.DATA)):
            for name in names:
                if name != 'report.html':
                    with open(os.path.join(root, name), 'rb') as handle:
                        self.assertNotIn(secret.encode(), handle.read(), name)

    def test_cancel_stops_a_running_job_and_removes_a_queued_one(self):
        cookie, other = self.login(), self.login('bob')
        os.environ['STUB_SLEEP'] = '60'
        ids = []
        try:
            workers = sum(1 for t in threading.enumerate() if t.name == 'job-worker')
            for _ in range(workers + 1):
                jid = self.make_job(cookie)
                self.put(cookie, jid, 'p/s.jsonl')
                self.api('POST', '/api/jobs/%s/start' % jid, cookie)
                ids.append(jid)
            for _ in range(50):
                if any(json.loads(self.api('GET', '/api/jobs/' + j, cookie)[1])['status'] == 'running' for j in ids):
                    break
                time.sleep(0.2)
            time.sleep(1.5)
            state = {j: json.loads(self.api('GET', '/api/jobs/' + j, cookie)[1])['status'] for j in ids}
            running = [j for j in ids if state[j] == 'running']
            queued = [j for j in ids if state[j] == 'queued']
            self.assertTrue(running and queued, state)
            self.assertEqual(self.api('POST', '/api/jobs/%s/cancel' % running[0], other)[0], 403)
            status, data = self.api('POST', '/api/jobs/%s/cancel' % queued[0], cookie)
            self.assertEqual((status, json.loads(data)['status']), (200, 'cancelled'))
            self.assertFalse(os.path.exists(os.path.join(config.JOBS, queued[0], 'home')))
            started = time.time()
            self.assertEqual(self.api('POST', '/api/jobs/%s/cancel' % running[0], cookie)[0], 200)
            row = self.wait(cookie, running[0], ('cancelled', 'done', 'failed'))
            self.assertEqual(row['status'], 'cancelled', row)
            self.assertLess(time.time() - started, 15)
            self.assertEqual(self.api('POST', '/api/jobs/%s/cancel' % running[0], cookie)[0], 409)
            self.assertEqual(self.api('POST', '/api/jobs/%s/delete' % running[0], cookie)[0], 200)
        finally:
            for jid in ids:
                self.api('POST', '/api/jobs/%s/cancel' % jid, cookie)
            for jid in ids:
                if os.path.isdir(os.path.join(config.JOBS, jid)):
                    self.wait(cookie, jid, ('cancelled', 'done', 'failed'))
            del os.environ['STUB_SLEEP']

    def test_failed_job_reports_reason(self):
        cookie = self.login()
        jid = self.make_job(cookie)
        self.put(cookie, jid, 'p/s.jsonl')
        os.environ['STUB_FAIL'] = '1'
        try:
            self.api('POST', '/api/jobs/%s/start' % jid, cookie)
            row = self.wait(cookie, jid)
        finally:
            del os.environ['STUB_FAIL']
        self.assertEqual(row['status'], 'failed')
        self.assertIn('退出码 3', row['message'])
        self.assertIn('boom', row['message'])


if __name__ == '__main__':
    unittest.main()
