import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from crm.collector import RunState
from crm.setup import NOTION_FIELDS
from crm.submissions import Ledger
from crm.transfer import allowed, export_workspace, restore_workspace, verify_archive


ROOT = Path(__file__).resolve().parents[1]


class TransferTests(unittest.TestCase):
    def workspace(self, root):
        root.mkdir()
        config = json.loads((ROOT / 'config.example.json').read_text(encoding='utf-8'))
        for key in NOTION_FIELDS:
            config['notion'][key] = '11111111-2222-4333-8444-555555555555'
        config['delivery']['live_sending'] = True
        config['cv'] = {'pdf_path': str(root / '.local' / 'candidate.pdf')}
        (root / 'config.local.json').write_text(json.dumps(config), encoding='utf-8')
        ledger = Ledger(root / '.local' / 'applications.db')
        with ledger.db:
            ledger.db.execute("INSERT INTO outbox VALUES ('known-receipt', '{}', 1)")
        ledger.close()
        state = RunState(root / '.local' / 'collector.db')
        token = state.acquire()
        state.checkpoint(token, 'gmail', {'window_end': 100}, complete=True, writes_verified=True)
        state.release(token)
        state.db.close()
        (root / '.local' / 'candidate.pdf').write_bytes(b'current CV')
        (root / '.local' / 'run-secret.token').write_text('do not transfer')
        (root / '.local' / 'CONTINUATION.md').write_text('Private next steps')
        (root / 'outputs').mkdir()
        (root / 'outputs' / 'pending.json').write_text('{"pending_label_ids":["message"]}')
        (root / 'outputs' / 'cookies.json').write_text('do not transfer')
        (root / 'AGENTS.md').write_text('Owner instructions')
        return root / '.local' / 'transfers' / 'continuation.zip'

    def test_round_trip_keeps_dedup_cursors_pending_work_and_cv_but_not_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, new = root / 'old', root / 'new'
            archive = self.workspace(old)
            exported = export_workspace(old, archive)
            self.assertFalse(exported['encrypted'])
            manifest = verify_archive(archive)
            self.assertNotIn('.local/run-secret.token', manifest['files'])
            self.assertNotIn('outputs/cookies.json', manifest['files'])
            new.mkdir()
            (new / '.git' / 'info').mkdir(parents=True)
            with patch.object(Path, 'rename', side_effect=PermissionError('Synced directory locked')):
                restored = restore_workspace(new, archive)
            self.assertFalse(restored['live_sending'])
            config = json.loads((new / 'config.local.json').read_text())
            self.assertEqual(config['cv']['pdf_path'], '.local/candidate.pdf')
            self.assertEqual((new / '.local' / 'candidate.pdf').read_bytes(), b'current CV')
            self.assertTrue((new / 'outputs' / 'pending.json').exists())
            self.assertIn('/AGENTS.md', (new / '.git' / 'info' / 'exclude').read_text())
            with closing(sqlite3.connect(new / '.local' / 'applications.db')) as db:
                self.assertEqual(db.execute('SELECT delivered FROM outbox').fetchone()[0], 1)
            with closing(sqlite3.connect(new / '.local' / 'collector.db')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM lease').fetchone()[0], 0)
                self.assertEqual(json.loads(db.execute('SELECT cursor FROM checkpoint').fetchone()[0]), {'window_end': 100})
            self.assertTrue(json.loads((old / 'config.local.json').read_text())['delivery']['live_sending'])

    def test_export_refuses_another_writer_and_releases_only_its_own_lease(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'old'
            archive = self.workspace(root)
            state = RunState(root / '.local' / 'collector.db')
            token = state.acquire()
            try:
                with self.assertRaises(RuntimeError):
                    export_workspace(root, archive)
                self.assertEqual(state.db.execute('SELECT token FROM lease').fetchone()[0], token)
                self.assertFalse(archive.exists())
            finally:
                state.release(token)
                state.db.close()

    def test_restore_refuses_existing_config_before_any_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old, new = root / 'old', root / 'new'
            archive = self.workspace(old)
            export_workspace(old, archive)
            new.mkdir()
            (new / 'config.local.json').write_text('preserve')
            with self.assertRaises(FileExistsError):
                restore_workspace(new, archive)
            self.assertEqual((new / 'config.local.json').read_text(), 'preserve')
            self.assertFalse((new / '.local').exists())

    def test_rejects_traversal_windows_aliases_and_tampered_archive_before_restore(self):
        for name in ('../outside', 'outputs/../../outside', '/outputs/a', 'outputs\\a',
                     'outputs/.. /a', 'outputs/CON.txt', 'outputs/file:stream', 'outputs/a.', 'outputs//a'):
            with self.subTest(name=name):
                self.assertFalse(allowed(name))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = self.workspace(root / 'old')
            export_workspace(root / 'old', archive)
            manifest = verify_archive(archive)
            changed = root / 'changed.zip'
            with zipfile.ZipFile(archive) as original, zipfile.ZipFile(changed, 'w') as target:
                for name in original.namelist():
                    data = original.read(name)
                    target.writestr(name, b'corrupted' if name == '.local/candidate.pdf' else data)
            with self.assertRaises(ValueError):
                restore_workspace(root / 'new', changed)
            self.assertFalse((root / 'new').exists())
            self.assertEqual(len(manifest['files']), 7)

    def test_export_blocks_embedded_password_and_never_writes_outside_private_transfers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'old'
            archive = self.workspace(root)
            with self.assertRaises(ValueError):
                export_workspace(root, root / 'public.zip')
            path = root / 'config.local.json'
            config = json.loads(path.read_text())
            config['email']['smtp_password'] = 'not exported'
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                export_workspace(root, archive)
            self.assertFalse(archive.exists())
