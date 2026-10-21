import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from json_storage import JsonStorage


class JsonStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=JsonStorage(self.root)
        with self.store.session() as store:
            store.put_device('WMS-001',1,hashlib.sha256(b'test').hexdigest())

    def tearDown(self):self.tmp.cleanup()

    def test_midnight_wib_restart_and_pagination(self):
        before=datetime(2026,10,21,23,59,59,tzinfo=ZoneInfo('Asia/Jakarta')).timestamp()
        with self.store.session() as store:
            first=store.append('WMS-001',before,-50,details={'environment':{'sensor_ok':False}})
            second=store.append('WMS-001',before+2,-60)
        paths=sorted(p.name for p in (self.root/'telemetry').glob('*.jsonl'))
        self.assertEqual(paths,['WMS-001-21-10-2026.jsonl','WMS-001-22-10-2026.jsonl'])
        doc=json.loads((self.root/'telemetry'/paths[0]).read_text().splitlines()[0])
        self.assertTrue(doc['received_at'].endswith('+07:00'))
        self.assertIsInstance(doc['details'],dict)
        fresh=JsonStorage(self.root)
        with fresh.session() as store:
            self.assertEqual(store.latest('WMS-001')['id'],second)
            self.assertEqual([s['id'] for s in store.history('WMS-001',since=before-10)],[first,second])
            self.assertEqual([s['id'] for s in store.history('WMS-001',after_id=first,limit=1)],[second])
            self.assertIsNotNone(store.device('WMS-001'))

    def test_parallel_writes_do_not_lose_samples(self):
        def send(i):
            # Separate objects exercise the OS file lock, as well as threads.
            with JsonStorage(self.root).session() as store:
                return store.append('WMS-001',1000+i,-50)
        with ThreadPoolExecutor(max_workers=8) as pool:ids=list(pool.map(send,range(40)))
        with self.store.session() as store:
            rows=store.history('WMS-001')
        self.assertEqual(len(rows),40)
        self.assertEqual(len(set(ids)),40)
        self.assertEqual({s['received'] for s in rows},set(range(1000,1040)))

    def test_atomic_write_failure_preserves_original(self):
        with self.store.session() as store:store.append('WMS-001',1000,-50)
        path=next((self.root/'telemetry').glob('*.jsonl'));original=path.read_bytes()
        with patch('json_storage.os.fsync',side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError):
                with self.store.session() as store:store.append('WMS-001',1001,-60)
        self.assertEqual(path.read_bytes(),original)
        with self.store.session() as store:self.assertEqual(len(store.history('WMS-001')),1)
        self.assertEqual(list((self.root/'telemetry').glob('*.tmp')),[])

    def test_corrupt_file_is_not_silently_overwritten(self):
        with self.store.session() as store:store.append('WMS-001',1000,-50)
        path=next((self.root/'telemetry').glob('*.jsonl'));path.write_text('{bad json\n')
        with self.assertRaisesRegex(ValueError,'rusak'):
            with self.store.session() as store:store.append('WMS-001',1001,-60)
        self.assertEqual(path.read_text(),'{bad json\n')

    def test_state_rollback_and_live_ping_persist(self):
        with self.assertRaises(RuntimeError):
            with self.store.session() as store:
                store.put_device('WMS-002',2,'key')
                raise RuntimeError()
        with JsonStorage(self.root).session() as store:
            self.assertIsNone(store.device('WMS-002'))
            store.put_control('WMS-001',live_until=2000,ping_id=42,ping_target='192.168.0.1',ping_until=1000)
        with JsonStorage(self.root).session() as store:
            self.assertEqual(store.control('WMS-001')['ping_id'],42)
            self.assertEqual(store.control('WMS-001')['live_until'],2000)

    def test_append_keeps_existing_bytes_and_inode(self):
        with self.store.session() as store:store.append('WMS-001',1000,-50)
        path=next((self.root/'telemetry').glob('*.jsonl'))
        original=path.read_bytes();inode=path.stat().st_ino
        with self.store.session() as store:store.append('WMS-001',1001,-60)
        self.assertTrue(path.read_bytes().startswith(original))
        self.assertEqual(path.stat().st_ino,inode)
        self.assertEqual(len(path.read_text().splitlines()),2)

    def test_migrate_json_idempotent_preserves_original(self):
        path=self.root/'telemetry'/'WMS-001-01-01-1970.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'schema_version':1,'samples':[dict(id=1,device_id='WMS-001',received=1000,received_at='1970-01-01T07:16:40+07:00',rssi=-50,details={})]}))
        original=path.read_bytes()
        with self.store.session() as store:
            self.assertEqual(store.migrate_json(),1)
            self.assertEqual(store.migrate_json(),0)
            store.append('WMS-001',1001,-60)
            self.assertEqual(len(store.history('WMS-001')),2)
        self.assertEqual(path.read_bytes(),original)

    def test_interrupted_tail_recovers_without_losing_valid_records(self):
        with self.store.session() as store:store.append('WMS-001',1000,-50)
        path=next((self.root/'telemetry').glob('*.jsonl'))
        with path.open('ab') as handle:handle.write(b'{"id":')
        with self.store.session() as store:
            store.append('WMS-001',1001,-60)
            self.assertEqual(len(store.history('WMS-001')),2)
        backups=list(path.parent.glob('*.partial-*'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),b'{"id":')

    def test_id_path_validation(self):
        for value in ['../escape','x/y','x*','']:
            with self.assertRaises(ValueError):
                with self.store.session() as store:store.append(value,1000,-60)
