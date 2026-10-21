import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import server

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(server, 'DATA_DIR', Path(self.temp.name)/'data')
        self.patch.start()
        with server.connect():pass
        with server.connect() as db:
            db.put_device('WMS-001',1,hashlib.sha256(b'test').hexdigest())
    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()
    def insert(self, now, ok=True, ms=180, local=12):
        with server.connect() as db:
            db.append('WMS-001',now,-57,local,ok,ms,record_id=len(db.history('WMS-001'))+1)
    def set_details(self,details,record_id=None,received=None):
        with server.connect() as store:
            for path in store.paths('WMS-001'):
                doc=store.read_day(path)
                for sample in doc['samples']:
                    if (record_id is None or sample['id']==record_id) and (received is None or sample['received']==received):sample['details']=details
                path.write_text(''.join(json.dumps(row)+'\n' for row in doc['samples']))
                store.cache.pop(path,None)
                store.latest_cache.clear()

    def test_waiting_online_offline_and_window(self):
        self.assertIsNone(server.snapshot(1000)['floors'][0]['nodes'][0]['lastSeen'])
        self.insert(600)
        self.insert(1000, False, None, None)
        floor = server.snapshot(1010)['floors'][0]
        self.assertTrue(floor['nodes'][0]['online'])
        self.assertEqual(len(floor['history']), 1)
        self.assertFalse(floor['nodes'][0]['internet'][0]['ok'])
        self.assertIsNone(floor['history'][0]['value'])
        self.assertFalse(server.snapshot(1036)['floors'][0]['nodes'][0]['online'])
    def test_null_internet_not_counted(self):
        self.insert(1000, None, None)
        self.assertEqual(server.snapshot(1001)['floors'][0]['nodes'][0]['internet'], [])
    def test_validation(self):
        self.assertEqual(server.validate(dict(rssi=-57,local_ms=12,internet_ok=True,internet_ms=180)),(-57,12,True,180))
        for payload in [[], {}, {'rssi':True}, {'rssi':-57,'internet_ok':'true'}, {'rssi':-57,'internet_ok':True}, {'rssi':-57,'local_ms':float('nan')}, {'rssi':-57,'internet_ok':False,'internet_ms':20}]:
            with self.assertRaises(ValueError):server.validate(payload)
    def test_http_ingestion_auth_and_dashboard(self):
        import io
        class Socket:
            def __init__(self, raw): self.input=io.BytesIO(raw); self.output=io.BytesIO()
            def makefile(self,*args,**kwargs): return self.input
            def sendall(self,data): self.output.write(data)
            def settimeout(self,value): pass
        def request(method,path,payload=None,key='test',authorization=''):
            body=json.dumps(payload).encode() if payload is not None else b''
            raw=(f'{method} {path} HTTP/1.0\r\nContent-Type: application/json\r\nAuthorization: {authorization}\r\nX-Device-ID: WMS-001\r\nX-Device-Key: {key}\r\nContent-Length: {len(body)}\r\n\r\n').encode()+body
            sock=Socket(raw)
            with patch.object(server.Handler,'log_message'):
                server.Handler(sock,('127.0.0.1',1),None)
            head,body=sock.output.getvalue().split(b'\r\n\r\n',1)
            return int(head.split()[1]),json.loads(body)
        payload=dict(rssi=-57,local_ms=12,internet_ok=True,internet_ms=180)
        payload.update(services={'google':dict(dns_ok=True,tcp_ok=True,dns_ms=12,tcp_ms=24,age_ms=500)},firmware_version='1.2.0',boot_id='test-boot',sequence=1,
            wifi=dict(ssid='Office',bssid='AA:BB:CC:DD:EE:FF',channel=6,ip='192.168.0.10',disconnect_count=2),
            device_health=dict(uptime_s=120,free_heap_bytes=28000),
            ping=dict(job_id=1,target='192.168.0.1',sent=4,received=3,loss_percent=25,min_ms=2,avg_ms=4,max_ms=6,age_ms=1000),
            password='never-store-this')

        self.assertEqual(request('POST','/api/telemetry',payload,key='wrong')[0],401)
        with server.connect() as db:
            db.add_key('WMS-001',hashlib.sha256(b'additional').hexdigest())
        self.assertEqual(request('POST','/api/telemetry',{'rssi':200},key='additional')[0],400)
        self.assertEqual(request('POST','/api/telemetry',{'rssi':200})[0],400)
        self.assertEqual(request('POST','/api/telemetry',payload)[0],201)
        status,data=request('GET','/api/dashboard')
        self.assertEqual(status,200)
        node=data['floors'][0]['nodes'][0]
        self.assertTrue(node['online'])
        self.assertEqual(node['latency'],12)
        self.assertEqual(node['details']['wifi']['channel'],6)
        self.assertEqual(node['services']['google']['status'],'reachable')
        self.assertEqual(node['services']['youtube']['status'],'unknown')
        self.assertEqual(node['details']['ping']['received'],3)
        self.assertNotIn('never-store-this',json.dumps(data))
        self.assertTrue(node['internet'][0]['ok'])
        self.assertNotIn('key_hash',json.dumps(data))
        self.assertEqual(request('GET','/api/dashboard?device_id=missing')[0],404)
        self.assertEqual(request('GET','/api/dashboard?date=2026-01-01')[0],400)
        self.assertEqual(request('GET','/api/dashboard?device_id=WMS-001&date=bad')[0],400)
        code,dated=request('GET','/api/dashboard?device_id=WMS-001&date=2026-01-01')
        self.assertEqual(code,200)
        self.assertEqual(dated['view'],'history')
        self.assertEqual(dated['samples'],0)
        code,filtered=request('GET','/api/dashboard?device_id=WMS-001')
        self.assertEqual(code,200)
        self.assertEqual([n['id'] for f in filtered['floors'] for n in f['nodes']],['WMS-001'])
        self.assertEqual(request('GET','/data/state.json')[0],404)
        self.assertEqual(request('GET','/data/telemetry/WMS-001-21-10-2026.json')[0],404)
        self.assertEqual(request('POST','/api/v1/devices/WMS-001/ping',{'target':'127.0.0.1'})[0],400)
        code,job=request('POST','/api/v1/devices/WMS-001/ping',{'target':'1.1.1.1'})
        self.assertEqual(code,202)
        self.assertEqual(request('GET','/api/v1/devices/WMS-001/ping')[1]['job_id'],job['job_id'])
        self.assertEqual(request('GET','/api/device/control')[1]['ping']['job_id'],job['job_id'])
        status,data=request('GET','/api/v1/devices')
        self.assertEqual(status,200)
        self.assertEqual(data['devices'][0]['internet']['status'],'normal')
        self.assertEqual(request('GET','/api/v1/devices/WMS-001/latest')[1]['status'],'online')
        status,history=request('GET','/api/v1/devices/WMS-001/history?limit=1')
        self.assertEqual(status,200)
        self.assertEqual(len(history['items']),1)
        self.assertEqual(history['items'][0]['details']['firmware_version'],'1.2.0')
        cursor=history['next_after_id']
        self.assertEqual(request('GET',f'/api/v1/devices/WMS-001/history?after_id={cursor}')[1]['items'],[])
        self.assertEqual(request('GET','/api/v1/devices/WMS-001/history?limit=1001')[0],400)
        self.assertEqual(request('GET','/api/v1/devices/missing/latest')[0],404)
        self.assertEqual(request('GET','/api/openapi.json')[1]['openapi'],'3.0.3')
        with patch.dict('os.environ',{'NETPULSE_READ_TOKEN':'private'}):
            self.assertEqual(request('GET','/api/v1/devices')[0],401)
            self.assertEqual(request('GET','/api/v1/devices',authorization='Bearer private')[0],200)
        self.assertEqual(request('GET','/api/v1/devices')[0],200)
        self.assertEqual(request('GET','/api/device/control',key='wrong')[0],401)
        self.assertEqual(request('GET','/api/device/control')[1]['mode'],'telemetry')
        control=dict(mode='live',live_interval_s=2,live_duration_s=120)
        with patch.dict('os.environ',{'NETPULSE_READ_TOKEN':'private'}):
            self.assertEqual(request('POST','/api/v1/devices/WMS-001/control',control)[0],401)
            status,desired=request('POST','/api/v1/devices/WMS-001/control',control,authorization='Bearer private')
        self.assertEqual(status,200)
        self.assertEqual(desired['send_interval_s'],2)
        self.assertEqual(request('GET','/api/device/control')[1]['mode'],'live')
        self.assertEqual(request('POST','/api/v1/devices/WMS-001/control',dict(mode='telemetry',telemetry_interval_s=1))[0],400)
        self.assertEqual(request('POST','/api/v1/devices/unknown/control',control)[0],404)



    def test_details_validation(self):
        for payload in [{'wifi':{'channel':15}}, {'device_health':{'uptime_s':True}}, {'device_health':{'free_heap_bytes':float('nan')}}, {'wifi':{'ssid':'a\nb'}}, {'ping':{'received':9}}]:
            with self.assertRaises(ValueError):server.validate_details(payload)
        self.assertEqual(server.validate_details({'password':'secret','wifi':{'password':'secret','channel':6}}),{'wifi':{'channel':6}})

    def test_service_validation_and_staleness(self):
        valid={'google':dict(dns_ok=False,tcp_ok=False,dns_ms=2000,tcp_ms=None,age_ms=100)}
        details=server.validate_details(dict(services=valid))
        self.insert(1000)
        self.set_details(details)
        node=server.snapshot(1001)['floors'][0]['nodes'][0]
        self.assertEqual(node['services']['google']['status'],'dns_failed')
        self.assertEqual(server.snapshot(1040)['floors'][0]['nodes'][0]['services']['google']['status'],'stale')
        for result in [dict(dns_ok='yes',tcp_ok=True,dns_ms=1,tcp_ms=1,age_ms=1),dict(dns_ok=False,tcp_ok=True,dns_ms=1,tcp_ms=1,age_ms=1),dict(dns_ok=True,tcp_ok=True,dns_ms=1,tcp_ms=None,age_ms=1)]:
            with self.assertRaises(ValueError):server.validate_details(dict(services={'google':result}))

    def test_service_history_deduplicates_cached_measurements(self):
        self.insert(1000)
        first=dict(boot_id='boot1',services={'google':dict(dns_ok=True,tcp_ok=True,dns_ms=5,tcp_ms=20,age_ms=1000)})
        self.set_details(first)
        self.insert(1010)
        repeated=dict(boot_id='boot1',services={'google':dict(dns_ok=True,tcp_ok=True,dns_ms=5,tcp_ms=20,age_ms=11000)})
        self.set_details(repeated,record_id=2)
        self.insert(1020)
        new=dict(boot_id='boot1',services={'google':dict(dns_ok=False,tcp_ok=False,dns_ms=2000,tcp_ms=None,age_ms=0)})
        self.set_details(new,record_id=3)
        history=server.snapshot(1021)['floors'][0]['nodes'][0]['service_history']
        self.assertEqual(len(history['google']),2)
        self.assertEqual(history['google'][0]['time'],999000)
        self.assertEqual(history['google'][0]['tcp_ms'],20)
        self.assertFalse(history['google'][1]['dns_ok'])
        self.assertEqual(history['youtube'],[])

    def test_gateway_ping_is_separate_from_http_and_deduplicated(self):
        gateway=dict(measurement_id=1,target='192.168.0.1',sent=1,received=1,loss_percent=0,min_ms=4,avg_ms=4,max_ms=4,age_ms=1000)
        details=server.validate_details({'boot_id':'b','gateway_ping':gateway,'wifi':{'gateway':'192.168.0.1'}})
        with server.connect() as store:
            store.append('WMS-001',1000,-60,123,details=details)
            details['gateway_ping']['age_ms']=11000
            store.append('WMS-001',1010,-60,123,details=details)
            details['gateway_ping'].update(measurement_id=2,received=0,loss_percent=100,min_ms=None,avg_ms=None,max_ms=None,age_ms=0)
            store.append('WMS-001',1020,-60,123,details=server.validate_details(details))
        node=server.snapshot(1021)['floors'][0]['nodes'][0]
        self.assertEqual([p['value'] for p in node['gateway_history']],[4,None])
        summary=server.summary_24h(1021)
        self.assertEqual(summary['gateway']['tests'],2)
        self.assertEqual(summary['gateway']['failed'],1)
        self.assertEqual(summary['gateway']['average_ms'],4)
        self.assertEqual(summary['local']['average_ms'],123)
        self.assertEqual(sum(h['gateway']['tests'] for h in summary['hourly']),2)
        with self.assertRaises(ValueError):server.validate_details({'gateway_ping':gateway,'wifi':{'gateway':'192.168.1.1'}})
        with self.assertRaises(ValueError):server.validate_details({'gateway_ping':dict(gateway,avg_ms=None)})

    def test_old_http_reports_are_not_gateway_measurements(self):
        self.insert(1000,local=222)
        node=server.snapshot(1001)['floors'][0]['nodes'][0]
        self.assertEqual(node['gateway_history'],[])
        self.assertEqual(server.summary_24h(1001)['gateway']['tests'],0)
        self.assertEqual(server.summary_24h(1001)['local']['average_ms'],222)

    def test_service_history_keeps_original_cadence_when_live_starts(self):
        def report(timestamp,mode,interval,value):
            with server.connect() as store:
                store.append('WMS-001',timestamp,-60,12,details={'delivery':{'mode':mode,'send_interval_s':interval,'telemetry_interval_s':60,'revision':1},'services':{'google':{'dns_ok':True,'tcp_ok':True,'dns_ms':5,'tcp_ms':value,'age_ms':0}}})
        report(1000,'telemetry',60,20)
        report(1240,'live',2,25)
        node=server.snapshot(1241)['floors'][0]['nodes'][0]
        points=node['service_history']['google']
        self.assertEqual(points[0]['max_gap_ms'],300000)
        self.assertEqual(points[1]['max_gap_ms'],90000)
        self.assertEqual(node['history'][0]['max_gap_ms'],90000)
        self.assertEqual(node['history'][1]['max_gap_ms'],35000)

    def test_24h_summary_buckets_failures_and_deduplication(self):
        self.insert(13000)
        self.insert(90000,local=10)
        sample=dict(boot_id='b',services={'google':dict(dns_ok=True,tcp_ok=True,dns_ms=10,tcp_ms=20,age_ms=1000)})
        self.set_details(sample,received=90000)
        self.insert(90010,local=20)
        sample['services']['google']['age_ms']=11000
        self.set_details(sample,received=90010)
        self.insert(94000,local=200)
        sample['services']['google']=dict(dns_ok=True,tcp_ok=False,dns_ms=10,tcp_ms=1500,age_ms=0)
        self.set_details(sample,received=94000)
        self.insert(94010,local=None)
        summary=server.summary_24h(100000)
        self.assertEqual(summary['local']['tests'],4)
        self.assertEqual(summary['local']['failed'],1)
        self.assertEqual(summary['local']['p95_ms'],200)
        self.assertEqual(summary['services']['google']['total']['tests'],2)
        self.assertEqual(summary['services']['google']['total']['average_ms'],30)
        self.assertEqual(summary['services']['google']['total']['success_percent'],50)
        self.assertEqual(summary['services']['youtube']['total']['tests'],0)
        self.assertEqual(summary['slowest_hours']['local']['hour_start_ms'],93600000)
        self.assertEqual(summary['slowest_hours']['local']['average_ms'],200)

    def test_temperature_validation_history_and_network_separation(self):
        with server.connect() as db:
            db.put_device('TMP-001',1,'hash',kind='temperature')
        good=dict(environment=dict(sensor_ok=True,temperature_c=24.5,humidity_percent=60,reading_age_ms=100))
        details=server.validate_details(good)
        with server.connect() as db:
            db.append('TMP-001',1000,-60,details=details)
        result=server.environment_snapshot(1001)['devices'][0]
        self.assertEqual(result['temperature_c'],24.5)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['stats_24h']['samples'],1)
        self.assertEqual(len(server.snapshot(1001)['floors']),1)
        self.assertEqual(server.summary_24h(1001)['local']['tests'],0)
        self.assertEqual(server.environment_snapshot(1100)['devices'][0]['status'],'offline')
        self.assertIsNone(server.environment_snapshot(1100)['devices'][0]['temperature_c'])
        self.assertIn('TMP-001',[d['id'] for d in server.device_list()['devices']])
        for env in [dict(sensor_ok=True,temperature_c=90,humidity_percent=60,reading_age_ms=0),dict(sensor_ok=False,temperature_c=20,humidity_percent=60,reading_age_ms=0),dict(sensor_ok=True,temperature_c=float('nan'),humidity_percent=60,reading_age_ms=0)]:
            with self.assertRaises(ValueError):server.validate_details(dict(environment=env))
        self.assertIsNone(server.validate_details(dict(environment=dict(sensor_ok=False,temperature_c=None,humidity_percent=None,reading_age_ms=0)))['environment']['temperature_c'])

    def test_remote_ping_queue_result_and_expiry(self):
        import time
        now=time.time()
        self.insert(now)
        job=server.request_ping('WMS-001',{'target':'192.168.0.1'})
        self.assertEqual(job['status'],'queued')
        with server.connect() as db:
            command=server.delivery_config(db,'WMS-001')['ping']
            self.assertEqual(command['job_id'],job['job_id'])
            self.assertEqual(server.ping_status(db,'WMS-001',now+65)['status'],'expired')
        with self.assertRaises(ValueError):server.request_ping('WMS-001',{'target':'1.1.1.1'})
        result=dict(job_id=job['job_id'],target=job['target'],sent=4,received=3,loss_percent=25,min_ms=1,avg_ms=2,max_ms=3,age_ms=0)
        result['packets']=[dict(seq=0,bytes=64,ms=2,ok=True),dict(seq=1,bytes=0,ms=0,ok=False)]
        self.assertEqual(server.validate_details({'ping':result})['ping']['packets'],result['packets'])
        with self.assertRaises(ValueError):server.validate_details({'ping':dict(result,packets=result['packets']*3)})
        with self.assertRaises(ValueError):server.validate_details({'ping':dict(result,packets=[{'seq':0}])})
        with server.connect() as db:
            db.append('WMS-001',time.time(),-60,details={'ping':result})
            self.assertEqual(server.ping_status(db,'WMS-001')['status'],'completed')
            self.assertIsNone(server.delivery_config(db,'WMS-001')['ping'])
        for target in ['google.com','127.0.0.1','224.0.0.1','0.0.0.0','192.168.0.255']:
            with self.assertRaises(ValueError):server.request_ping('WMS-001',{'target':target})
        with self.assertRaises(LookupError):server.request_ping('missing',{'target':'1.1.1.1'})

    def test_filtered_snapshot_reads_only_selected_device(self):
        with server.connect() as store:store.put_device('WMS-002',2,'hash')
        calls=[]
        from json_storage import JsonStorage
        original=JsonStorage.latest
        def latest(store,device_id):
            calls.append(device_id)
            return original(store,device_id)
        with patch.object(JsonStorage,'latest',latest):data=server.snapshot(1000,device_id='WMS-001')
        self.assertEqual([n['id'] for f in data['floors'] for n in f['nodes']],['WMS-001'])
        self.assertNotIn('WMS-002',calls)

    def test_filtered_stream_sends_heartbeat_for_unchanged_device(self):
        import io
        handler=server.Handler.__new__(server.Handler)
        class Stream(io.BytesIO):
            def __init__(self):super().__init__();self.writes=0
            def write(self,data):
                self.writes+=1
                if self.writes>2:raise BrokenPipeError()
                return super().write(data)
        handler.wfile=Stream();handler.send_response=lambda code:None
        handler.send_header=lambda name,value:None;handler.end_headers=lambda:None
        with patch.object(server.UPDATES,'wait_for',return_value=True):handler.stream_dashboard('WMS-001')
        data=handler.wfile.getvalue().decode()
        self.assertEqual(data.count('event: dashboard'),1)
        self.assertIn(': keep-alive',data)
        self.assertNotIn('WMS-002',data)

    def test_device_daily_report_wib_boundaries_and_empty_day(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        start=datetime(2026,1,1,tzinfo=ZoneInfo('Asia/Jakarta')).timestamp()
        with server.connect() as store:
            store.put_device('WMS-002',2,'hash')
            store.append('WMS-001',start-1,-60,999)
            store.append('WMS-001',start+60,-60,20,details={'services':{'google':{'dns_ok':True,'tcp_ok':True,'dns_ms':5,'tcp_ms':15,'age_ms':0}}})
            store.append('WMS-001',start+86400,-60,888)
            store.append('WMS-002',start+60,-60,999)
        report=server.device_day('WMS-001','2026-01-01')
        self.assertEqual(report['samples'],1)
        self.assertEqual(report['summary_24h']['to_ms']-report['summary_24h']['from_ms'],86400000)
        with patch.object(server.time,'time',return_value=start+3600):
            today=server.device_day('WMS-001','2026-01-01')
        self.assertEqual(today['summary_24h']['to_ms'],(start+86400)*1000)
        self.assertEqual(today['samples'],1)
        self.assertEqual(report['summary_24h']['local']['average_ms'],20)
        self.assertEqual(report['summary_24h']['services']['google']['total']['average_ms'],20)
        self.assertEqual(report['floors'][0]['nodes'][0]['latency'],20)
        self.assertEqual(server.device_day('WMS-001','2026-01-03')['samples'],0)
        with self.assertRaises(ValueError):server.device_day('WMS-001','2026-02-30')
        with self.assertRaises(ValueError):server.device_day('WMS-001','2999-01-01')
        with self.assertRaises(LookupError):server.device_day('missing','2026-01-01')

    def test_overview_has_individual_summaries_without_graph_history(self):
        with server.connect() as store:
            store.put_device('WMS-002',2,'hash')
            store.append('WMS-001',1000,-60,10)
            store.append('WMS-002',1000,-60,200)
        with patch.object(server.time,'time',return_value=1001),patch.dict(server.OVERVIEW_CACHE,{'until':0,'data':None}):
            data=server.overview()
        nodes={n['id']:n for f in data['floors'] for n in f['nodes']}
        self.assertEqual(nodes['WMS-001']['summary_24h']['local']['average_ms'],10)
        self.assertEqual(nodes['WMS-002']['summary_24h']['local']['average_ms'],200)
        self.assertEqual(nodes['WMS-001']['history'],[])
        self.assertNotIn('hourly',nodes['WMS-001']['summary_24h'])

    def test_dual_feature_modes(self):
        def report(network, temperature, timestamp):
            data={'features':{'network':network,'temperature':temperature}}
            if temperature:data['environment']={'sensor_ok':True,'temperature_c':25.0,'humidity_percent':65.0,'reading_age_ms':100}
            data=server.validate_details(data)
            with server.connect() as db:
                db.append('WMS-001',timestamp,-60,12 if network else None,details=data)
        report(True,True,1000)
        self.assertEqual(len(server.snapshot(1001)['floors']),1)
        self.assertEqual(server.environment_snapshot(1001)['devices'][0]['id'],'WMS-001')
        self.assertEqual(len(server.device_list()['devices']),1)
        report(False,True,1010)
        self.assertEqual(server.snapshot(1011)['floors'],[])
        self.assertEqual(len(server.environment_snapshot(1011)['devices']),1)
        self.assertEqual(server.summary_24h(1011)['local']['tests'],1)
        report(True,False,1020)
        self.assertEqual(len(server.snapshot(1021)['floors']),1)
        self.assertEqual(server.environment_snapshot(1021)['devices'],[])

    def test_device_page_route(self):
        import io
        class Socket:
            def __init__(self,path):
                self.input=io.BytesIO(f'GET {path} HTTP/1.0\r\n\r\n'.encode());self.output=io.BytesIO()
            def makefile(self,*args,**kwargs):return self.input
            def sendall(self,data):self.output.write(data)
            def settimeout(self,value):pass
        for path,code in [('/devices/WMS-001',200),('/devices/missing',404),('/devices/../server.py',404)]:
            sock=Socket(path)
            with patch.object(server.Handler,'log_message'):
                server.Handler(sock,('127.0.0.1',1),None)
            response=sock.output.getvalue()
            self.assertEqual(int(response.split()[1]),code)
            if code==200:
                self.assertIn(b'text/html',response)
                self.assertIn(b'device-live',response)
                self.assertIn(b'id="device-inspector"',response)
                self.assertNotIn(b'id="floors"',response)
                self.assertIn(b'/assets/dashboard.js',response)

    def test_live_stream_framing_and_disconnect(self):
        import io
        handler=server.Handler.__new__(server.Handler)
        class Stream(io.BytesIO):
            def __init__(self):super().__init__();self.writes=0
            def write(self,data):
                self.writes+=1
                if self.writes>2:raise BrokenPipeError()
                return super().write(data)
        handler.wfile=Stream()
        handler.send_response=lambda code:self.assertEqual(code,200)
        headers={}
        handler.send_header=lambda name,value:headers.update({name:value})
        handler.end_headers=lambda:None
        with patch.object(server.UPDATES,'wait_for',return_value=True):
            handler.stream_dashboard()
        self.assertEqual(headers['Content-Type'],'text/event-stream; charset=utf-8')
        events=handler.wfile.getvalue().decode().strip().split('\n\n')
        self.assertEqual(len(events),2)
        for event in events:
            self.assertTrue(event.startswith('event: dashboard\ndata: '))
            self.assertIn('floors',json.loads(event.split('data: ',1)[1]))
        self.assertTrue(handler.close_connection)

    def test_live_lease_expiry_and_interval_offline_threshold(self):
        with patch.object(server.time,'time',return_value=1000):
            desired=server.update_delivery('WMS-001',dict(mode='live',live_interval_s=5,live_duration_s=30))
        with server.connect() as db:
            self.assertEqual(server.delivery_config(db,'WMS-001',1020)['mode'],'live')
            self.assertEqual(server.delivery_config(db,'WMS-001',1020)['send_interval_s'],5)
            config=server.delivery_config(db,'WMS-001',1031)
            self.assertEqual(config['mode'],'telemetry')
            self.assertEqual(config['send_interval_s'],10)
        self.insert(1000)
        with server.connect() as db:
            details=dict(delivery=dict(mode='telemetry',send_interval_s=120,telemetry_interval_s=120,revision=desired['revision']))
            self.set_details(details)
        node=server.snapshot(1100)['floors'][0]['nodes'][0]
        self.assertTrue(node['online'])
        self.assertEqual(node['control']['telemetry_interval_s'],120)
        self.assertEqual(node['offline_after_s'],365)
        self.assertFalse(server.snapshot(1366)['floors'][0]['nodes'][0]['online'])
        for payload in [dict(mode='invalid'),dict(mode='live',live_interval_s=True),dict(mode='live',live_duration_s=121),dict(mode='telemetry',telemetry_interval_s=60),dict(mode='live',live_interval_s=31)]:
            with self.assertRaises(ValueError):server.update_delivery('WMS-001',payload)

    def test_json_rooms_and_multiple_devices_per_floor(self):
        config=Path(self.temp.name)/'devices.json'
        config.write_text(json.dumps({'devices':[
            {'id':'WMS-001','floor':1,'room':'Lobi','name':'Lobi'},
            {'id':'WMS-002','floor':1,'room':'Rapat','device_key':'testkey123'}]}))
        server.apply_device_config(config)
        data=server.snapshot(1000)
        self.assertEqual(len(data['floors']),1)
        self.assertEqual(len(data['floors'][0]['nodes']),2)
        self.assertEqual(data['floors'][0]['nodes'][0]['position'],'Lobi')
        self.assertEqual(len(server.device_list()['devices']),2)
        config.write_text(json.dumps({'devices':[{'id':'WMS-001','floor':2,'room':'Changed'},{'id':'NEW','floor':1}]}))
        with self.assertRaises(ValueError):server.apply_device_config(config)
        self.assertEqual(server.snapshot()['floors'][0]['nodes'][0]['position'],'Lobi')


if __name__=='__main__':unittest.main()
