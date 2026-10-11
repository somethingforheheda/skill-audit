"""Offline HTML serialization and output-contract checks."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import history_export as export
import skill_audit_cli as cli
import agent_backends.codex as codex_backend

class ExportTests(unittest.TestCase):
    def report(self):
        return {'extraction':{'reads':[],'summary':{},'limitations':[]},'sessions':[],'scope':{},'coverage':{}}
    def test_escapes_script_breakout_and_roundtrips(self):
        data=self.report();data['extraction']['limitations']=['</script><script>alert(1)</script>&\u2028']
        html=export.render_html(data)
        self.assertNotIn('</script><script>alert(1)',html)
        payload=html.split('<script id="report-data" type="application/json">')[1].split('</script>')[0]
        self.assertEqual(json.loads(payload)['extraction'],data['extraction'])
        self.assertNotIn('__SKILL_AUDIT_DATA__',html)
    def test_no_snapshot_contents_embedded(self):
        data=self.report();data['sessions']=[{'session_id':'one','source':'codex','events':['PRIVATE_RAW_HISTORY'],
                                             'manifest':'/tmp/run/sessions/abc/session.json','project':'/work'}]
        html=export.render_html(data)
        self.assertNotIn('PRIVATE_RAW_HISTORY',html)
        self.assertNotIn('/tmp/run/sessions/abc/session.json',html)

    def test_presentation_fields_reach_the_page(self):
        data=self.report()
        data['schema_version']='1.1';data['kind']='history_evidence'
        data['period']={'from':'1970-01-01T00:00:00+00:00','to_exclusive':'2026-09-29T04:56:05+00:00','timezone':'CST'}
        data['sessions']=[{'session_id':'child','source':'codex','last_active':'2026-09-29T04:00:00+00:00',
                           'parent_session':'root','candidates':['a1'],'problems':[],'snapshot_sha256':'ff',
                           'events':['RAW'],'manifest':'/tmp/x/session.json'}]
        payload=json.loads(export.render_html(data).split('<script id="report-data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(payload['period']['timezone'],'CST')
        self.assertEqual(payload['schema_version'],'1.1')
        session=payload['sessions'][0]
        self.assertEqual(session['parent_session'],'root')
        self.assertEqual(session['candidates'],['a1'])
        self.assertEqual(session['snapshot_sha256'],'ff')
        self.assertNotIn('events',session)
        self.assertNotIn('manifest',session)
    def test_export_creates_html_only_and_preserves_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json';path.write_text(json.dumps(self.report()))
            original=path.read_bytes();out=export.export(self.report(),path)
            self.assertEqual(out.name,'report.html');self.assertEqual(path.read_bytes(),original)
            self.assertFalse(path.with_suffix('.csv').exists());self.assertTrue(out.is_file())
    def test_cli_export_no_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json';path.write_text(json.dumps(self.report()))
            with patch.object(codex_backend,'run_codex',side_effect=AssertionError('no model')):
                self.assertEqual(cli.main(['history-export',str(path)]),0)
            self.assertTrue((path.with_suffix('')/'report.html').is_file())
    def test_collect_only_produces_json_and_html(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            with patch.object(codex_backend,'run_codex',side_effect=AssertionError('no model')):
                self.assertEqual(cli.main(['history','--collect-only','--history-home',directory,'--all-projects','--sources','codex','--output',str(path)]),0)
            folder=path.with_suffix('')
            self.assertTrue((folder/'report.json').is_file());self.assertTrue((folder/'report.html').is_file())
            self.assertTrue((folder/'snapshots').is_dir())
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix('.csv').exists())
    def test_directory_output_and_legacy_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)/'bundle'
            self.assertEqual(export.report_directory(base.with_suffix('.json')),base)
            (base/'report.json').write_text('keep')
            with self.assertRaises(ValueError):export.report_directory(base)
            self.assertEqual((base/'report.json').read_text(),'keep')

    def test_rejects_non_extraction_report(self):
        with self.assertRaises(ValueError):export.render_html({'status':'complete'})

    def test_matching_sidecar_embedded_but_stale_analysis_hidden(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            data=self.report();path.write_text(json.dumps(data))
            original=path.read_bytes()
            sidecar=path.parent/'analysis.json'
            sidecar.write_text(json.dumps({'input_sha256':hashlib.sha256(original).hexdigest(),'overview':'SIDECAR_SENTINEL','findings':[]}))
            output=export.export(data,path)
            self.assertIn('SIDECAR_SENTINEL',output.read_text())
            path.write_text(json.dumps({**data,'kind':'changed'}))
            export.export(data,path)
            self.assertNotIn('SIDECAR_SENTINEL',output.read_text())

    def test_cli_reexport_preserves_matching_analysis_without_editing_source(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json';path.write_text(json.dumps(self.report()))
            original=path.read_bytes()
            (path.parent/'analysis.json').write_text(json.dumps({'input_sha256':hashlib.sha256(original).hexdigest(),'overview':'OFFLINE_REEXPORT','findings':[]}))
            self.assertEqual(cli.main(['history-export',str(path)]),0)
            self.assertEqual(path.read_bytes(),original)
            self.assertIn('OFFLINE_REEXPORT',(path.with_suffix('')/'report.html').read_text())

if __name__=='__main__':unittest.main()
