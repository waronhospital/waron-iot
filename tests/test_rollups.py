import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server
from json_storage import JsonStorage

class RollupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(server,'DATA_DIR',Path(self.temp.name));self.patch.start()
        with server.connect() as store:store.put_device('R-1',1,'hash')
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def append(self,t,seq):
        result={'dns_ok':True,'tcp_ok':seq%3!=0,'dns_ms':5,'tcp_ms':20,'age_ms':0}
        with server.connect() as store:
            store.append('R-1',t,-60,seq+1,details={'boot_id':'a','services':{'google':result}})
    def compare(self,start,end):
        with server.connect() as store:rows=store.history('R-1',since=start,raw=True)
        expected=server.summary_24h(now=end,start=start,device_id='R-1',_rows=rows)
        actual=server.summary_24h(now=end,start=start,device_id='R-1')
        self.assertEqual(actual,expected)
    def test_matches_raw_across_hours_and_midnight(self):
        for seq,t in enumerate([86399,86400,86401,90000,93600,97000]):self.append(t,seq)
        for start,end in [(86398,98000),(86400,98000),(86400.5,97000),(89000,95000)]:self.compare(start,end)
    def test_restarts_without_reading_raw_history(self):
        self.append(90000,1)
        fresh=JsonStorage(Path(self.temp.name))
        with fresh.session():
            with patch.object(fresh,'read_day',side_effect=AssertionError('raw day parsed')):
                self.assertEqual(fresh.latest('R-1')['local_ms'],2)
                self.assertEqual(fresh.rollups.summary(['R-1'],89000,92000)['local']['tests'],1)
    def test_missing_and_corrupt_summary_rebuilds(self):
        self.append(90000,1)
        for corrupt in [False,True]:
            target=next((Path(self.temp.name)/'summaries').glob('*.json'))
            if corrupt:target.write_text('{broken')
            else:target.unlink()
            with server.connect() as store:store.rollups.cache.clear()
            self.compare(89000,92000)
    def test_new_sample_updates_without_reparsing_old_lines(self):
        self.append(90000,1)
        with server.connect() as store:
            before=next(iter(store.rollups.cache.values()))['offset']
        self.append(90060,2)
        with server.connect() as store:
            doc=next(iter(store.rollups.cache.values()))
            self.assertGreater(doc['offset'],before)
            self.assertEqual(doc['latest']['local_ms'],3)
        self.compare(89000,92000)
    def test_historical_device_page_uses_materialized_view(self):
        self.append(90000,1)
        with server.connect() as store:
            with patch.object(store,'history',side_effect=AssertionError('history loaded')):
                result=server.device_day('R-1','1970-01-02')
        self.assertEqual(result['samples'],1)
        self.assertEqual(result['floors'][0]['nodes'][0]['latency'],2)
    def test_partial_line_then_complete(self):
        self.append(90000,1)
        with server.connect() as store:
            path=store.paths('R-1')[0]
            row={'id':99,'device_id':'R-1','received':90060,'rssi':-60,'local_ms':3,'details':{}}
            payload=json.dumps(row).encode()
            with path.open('ab') as file:file.write(payload[:20])
            self.assertEqual(store.rollups.read(path)['reports'],1)
            with path.open('ab') as file:file.write(payload[20:]+b'\n')
            self.assertEqual(store.rollups.read(path)['reports'],2)
    def test_external_rewrite_rebuilds(self):
        self.append(90000,1)
        with server.connect() as store:
            path=store.paths('R-1')[0]
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            rows[0]['local_ms']=123
            path.write_text(''.join(json.dumps(row,indent=0)+'\n' for row in rows))
            # JSONL must stay one JSON value per line; rewrite compact with extra spaces.
            path.write_text(''.join(json.dumps(row,separators=(', ',': '))+'\n' for row in rows))
            self.assertEqual(store.rollups.read(path)['latest']['local_ms'],123)
    def test_environment_stats_and_partial_hours(self):
        with server.connect() as store:
            store.put_device('R-1',1,features={'network':False,'temperature':True})
            for t,c,h in [(90000,20,50),(90060,22,60),(93600,None,None)]:
                store.append('R-1',t,-60,details={'features':{'network':False,'temperature':True},'environment':{'sensor_ok':c is not None,'temperature_c':c,'humidity_percent':h,'reading_age_ms':0}})
            stats,hourly=store.rollups.environment('R-1',89999,94000)
            self.assertEqual(stats,dict(samples=2,failed=1,min_c=20,max_c=22,average_c=21,average_humidity=55))
            stats,_=store.rollups.environment('R-1',90030,94000)
            self.assertEqual(stats['samples'],1)
            self.assertEqual(stats['average_c'],22)
            with patch.object(store,'history',side_effect=AssertionError('raw history read')):
                data=server.environment_snapshot(now=94000)
                self.assertEqual(data['devices'][0]['stats_24h']['samples'],2)
