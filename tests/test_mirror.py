import argparse
import contextlib
import http.server
import io
import json
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import mactools_mirror as mirror


PAYLOAD = b'mactools bounded reference bottle\x00' * 2048


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/bad/hello.bottle.tar.gz':
            payload = b'tampered'
        elif self.path == '/404/hello.bottle.tar.gz':
            self.send_error(404)
            return
        elif self.path.endswith('/formula/hello.json'):
            payload = json.dumps({'name': 'hello', 'bottle': {'stable': {'files': {'arm64_tahoe': {'sha256': mirror.digest(PAYLOAD)}}}}}).encode()
        else:
            payload = PAYLOAD
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class MirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = Server(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(config_dir=str(self.root / 'config'), state_dir=str(self.root / 'state'), yes=True, force=False, dry_run=False, timeout=1, limit=8, mirror=[self.url + '/good', self.url + '/bad'])
        self.sample = {'url': self.url + '/official', 'filename': 'hello.bottle.tar.gz', 'sha256': mirror.digest(PAYLOAD), 'bytes': len(PAYLOAD)}

    def tearDown(self):
        self.temp.cleanup()

    def test_real_local_download_hash_speed_and_rejection(self):
        with patch.object(mirror, 'reference', return_value=self.sample):
            results = mirror.benchmark(self.args)
        self.assertEqual([r['ok'] for r in results], [True, False])
        self.assertGreater(results[0]['bytes_per_second'], 0)
        self.assertEqual(results[0]['bytes'], len(PAYLOAD))
        self.assertIn('SHA-256', results[1]['error'])
        failed = mirror.measure('missing', self.url + '/404', self.sample, 1)
        self.assertFalse(failed['ok'])

    def test_original_restored_exactly_and_mode_preserved(self):
        target = self.root / 'config' / 'mirrors.sh'
        target.parent.mkdir()
        original = b'# custom\r\nexport HOMEBREW_API_DOMAIN="old"\r\n'
        target.write_bytes(original)
        target.chmod(0o640)
        applied = mirror.apply(self.args, self.url)
        self.assertEqual(applied['action'], 'applied')
        self.assertTrue(Path(applied['backup']).exists())
        self.assertIn('source ', applied['source'])
        mirror.restore(self.args)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertFalse((self.root / 'state' / 'mirror-backup.json').exists())

    def test_new_fragment_removed_on_restore(self):
        mirror.apply(self.args, self.url)
        output = mirror.restore(self.args)
        self.assertFalse((self.root / 'config' / 'mirrors.sh').exists())
        self.assertTrue(output['source'].startswith('unset '))

    def test_restore_refuses_modified_config_unless_force(self):
        mirror.apply(self.args, self.url)
        target = self.root / 'config' / 'mirrors.sh'
        target.write_bytes(b'# user edit\n')
        with self.assertRaisesRegex(ValueError, 'changed since apply'):
            mirror.restore(self.args)
        self.assertEqual(target.read_bytes(), b'# user edit\n')
        self.args.force = True
        mirror.restore(self.args)
        self.assertFalse(target.exists())

    def test_dry_run_has_no_writes_or_confirmation(self):
        self.args.dry_run = True
        with patch.object(mirror, 'confirm', side_effect=AssertionError('confirmation in preview')):
            self.assertEqual(mirror.apply(self.args, self.url)['action'], 'preview')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_restore_preview_no_change(self):
        mirror.apply(self.args, self.url)
        target = self.root / 'config' / 'mirrors.sh'
        original = target.read_bytes()
        self.args.dry_run = True
        self.assertEqual(mirror.restore(self.args)['action'], 'restore-preview')
        self.assertEqual(target.read_bytes(), original)
        self.assertTrue((self.root / 'state' / 'mirror-backup.json').exists())

    def test_validation_failure_rolls_back_config_and_backup(self):
        mirror.apply(self.args, self.url)
        target = self.root / 'config' / 'mirrors.sh'
        manifest = self.root / 'state' / 'mirror-backup.json'
        before, backup = target.read_bytes(), manifest.read_bytes()
        with patch.object(mirror, 'validate_fragment', side_effect=ValueError('invalid')):
            with self.assertRaisesRegex(ValueError, 'invalid'):
                mirror.apply(self.args, self.url + '/second')
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(manifest.read_bytes(), backup)

    def test_symlink_refusal_does_not_change_external_file(self):
        target = self.root / 'config' / 'mirrors.sh'
        target.parent.mkdir()
        outside = self.root / 'outside'
        outside.write_bytes(b'keep')
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            mirror.apply(self.args, self.url)
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_corrupt_backup_refused(self):
        target = self.root / 'config' / 'mirrors.sh'
        target.parent.mkdir()
        target.write_bytes(b'keep')
        mirror.apply(self.args, self.url)
        manifest = self.root / 'state' / 'mirror-backup.json'
        data = json.loads(manifest.read_bytes())
        data['original'] = 'dGFtcGVyZWQ='
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            mirror.restore(self.args)

    def test_malformed_backup_schema_clear_error(self):
        mirror.apply(self.args, self.url)
        manifest = self.root / 'state' / 'mirror-backup.json'
        target = self.root / 'config' / 'mirrors.sh'
        original = target.read_bytes()
        for payload in ([], None, {}, {'version': 1}):
            with self.subTest(payload=payload):
                manifest.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, 'manifest schema'):
                    mirror.restore(self.args)
                self.assertEqual(target.read_bytes(), original)

    def test_bad_urls_rejected(self):
        for value in ('file:///tmp/a', 'https://user:pass@host/path', 'https://host/a?b=1', 'https://host/a\nexport X=1'):
            with self.assertRaises(ValueError):
                mirror.normalize(value)

    def test_bare_mirror_offers_apply_and_bare_json_tests_only(self):
        results = [{'url': self.url + '/good', 'name': 'local', 'ok': True, 'bytes': len(PAYLOAD), 'bytes_per_second': 100, 'sha256': mirror.digest(PAYLOAD)}]
        with patch.object(mirror, 'benchmark', return_value=results), patch.object(mirror, 'apply', return_value={'action': 'cancelled'}) as apply, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mirror.main([]), 0)
            apply.assert_called_once()
        with patch.object(mirror, 'benchmark', return_value=results), patch.object(mirror, 'apply', side_effect=AssertionError('test must not apply')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mirror.main(['--json']), 0)
            self.assertEqual(mirror.main(['test']), 0)

    def test_auto_rejects_bad_api_before_writing(self):
        results = [{'url': self.url + '/good', 'name': 'local', 'ok': True, 'bytes': len(PAYLOAD), 'bytes_per_second': 100, 'sha256': mirror.digest(PAYLOAD)}]
        with patch.object(mirror, 'benchmark', return_value=results), patch.object(mirror, 'fetch', return_value=b'{}'), patch.object(mirror, 'apply', side_effect=AssertionError('invalid API must not apply')), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'API validation failed'):
                mirror.main(['auto'])

    def test_null_api_schema_is_clear_error(self):
        results = [{'url': self.url + '/good', 'name': 'local', 'ok': True, 'sha256': mirror.digest(PAYLOAD)}]
        for payload in (b'null', b'[]', b'{"bottle":null}'):
            with patch.object(mirror, 'benchmark', return_value=results), patch.object(mirror, 'fetch', return_value=payload):
                with self.assertRaisesRegex(ValueError, 'API validation failed'):
                    mirror.main(['auto'])

    def test_auto_preview_with_local_server_is_read_only(self):
        with patch.object(mirror, 'reference', return_value=self.sample), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(mirror.main(['auto', '--mirror', self.url + '/good', '--config-dir', self.args.config_dir, '--state-dir', self.args.state_dir, '--dry-run', '--json']), 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data['action'], 'preview')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_official_reference_hash_and_filename(self):
        metadata = {'versions': {'stable': '2.12.2'}, 'revision': 1, 'bottle': {'stable': {'rebuild': 2, 'files': {'arm64_tahoe': {'sha256': mirror.digest(PAYLOAD), 'url': mirror.OFFICIAL_BOTTLES + '/hello/blobs/sha256:' + mirror.digest(PAYLOAD)}}}}}
        with patch.object(mirror.platform, 'machine', return_value='arm64'), patch.object(mirror, 'fetch', return_value=json.dumps(metadata).encode()), patch.object(mirror, 'bottle_fetch', return_value=PAYLOAD):
            sample = mirror.reference(1)
        self.assertEqual(sample['filename'], 'hello-2.12.2_1.arm64_tahoe.bottle.2.tar.gz')
        with patch.object(mirror.platform, 'machine', return_value='arm64'), patch.object(mirror, 'fetch', return_value=json.dumps(metadata).encode()), patch.object(mirror, 'bottle_fetch', return_value=b'tampered'):
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                mirror.reference(1)

    def test_invalid_official_metadata_clear_error(self):
        with patch.object(mirror, 'fetch', return_value=b'{}'):
            with self.assertRaisesRegex(ValueError, 'Invalid official'):
                mirror.reference(1)

    def test_oversized_bottle_rejected(self):
        with patch.object(mirror, 'SAMPLE_LIMIT', 32):
            result = mirror.measure('large', self.url, self.sample, 1)
        self.assertFalse(result['ok'])
        self.assertIn('size limit', result['error'])


if __name__ == '__main__':
    unittest.main()
