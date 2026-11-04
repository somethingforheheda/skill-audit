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
                  SA_MIN_FREE_GB='0', SA_MAX_FILE_MB='1', SA_JOB_TIMEOUT='60')
sys.path.insert(0, os.path.dirname(HERE))

import app  # noqa: E402
import config  # noqa: E402
import jobs  # noqa: E402
import runner  # noqa: E402

H = {'X-Requested-With': 'sa'}


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
        self.assertIn('上传历史会话'.encode(), data)

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
        self.assertFalse(os.path.exists(os.path.join(config.JOBS, jid, 'home')))
        res, _ = self.call('GET', '/r/%s/../job.json' % jid, cookie=cookie)
        self.assertEqual(res.status, 404)
        res, _ = self.call('GET', '/r/%s/report.html' % jid)
        self.assertEqual(res.status, 302)
        self.assertEqual(self.api('POST', '/api/jobs/%s/delete' % jid, self.login('bob'))[0], 403)
        self.assertEqual(self.api('POST', '/api/jobs/%s/delete' % jid, cookie)[0], 200)
        self.assertIsNone(jobs.get(jid))

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
