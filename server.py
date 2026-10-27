"""WARON IoT local backend. Python 3.9+, no external dependencies."""
import controller_proxy
import argparse
from datetime import datetime, timedelta
import ipaddress
import hashlib
import hmac
import json
import math
import os
from urllib.parse import urlsplit, parse_qs, unquote
import secrets
import time
import threading
from json_storage import JsonStorage
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / 'data'
STORES = {}
CONFIG = ROOT / 'config' / 'devices.json'
UPDATES = threading.Condition()
REVISION = 0
CONFIG_WATCH_PATH = None
CONFIG_WATCH_SIGNATURE = None
CONFIG_RELOAD_LOCK = threading.Lock()

def notify_update():
    global REVISION
    with UPDATES:
        REVISION += 1
        UPDATES.notify_all()


def connect():
    root=Path(DATA_DIR).resolve()
    with STORE_LOCK:
        store=STORES.get(root)
        if store is None:
            store=JsonStorage(root)
            STORES[root]=store
    return store.session()


STORE_LOCK=threading.Lock()

def init_storage():
    with connect() as store:
        store.migrate_json()
        if 'migrations' in store.state:
            store.state.pop('migrations')
            store.dirty=True


def apply_device_config(path):
    import re
    config = json.loads(Path(path).read_text())
    if not isinstance(config,dict) or not isinstance(config.get('devices'),list):
        raise ValueError('Konfigurasi harus berisi array devices')
    checked=[]; seen=set()
    for item in config['devices']:
        if not isinstance(item,dict): raise ValueError('Device harus objek')
        device_id=item.get('id')
        if not isinstance(device_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',device_id) or device_id in seen:
            raise ValueError('ID perangkat tidak valid atau duplikat')
        seen.add(device_id)
        floor=item.get('floor')
        if type(floor) is not int or not 1<=floor<=999: raise ValueError('floor harus 1–999')
        metadata={}
        for field,default in [('name',device_id),('building','Gedung Utama'),('room','Titik pantau utama')]:
            value=item.get(field,default)
            if not isinstance(value,str) or not value.strip() or len(value)>100 or any(ord(c)<32 for c in value): raise ValueError(field+' harus teks 1–100 karakter')
            metadata[field]=value.strip()
        kind=item.get('kind','network')
        if kind not in ('network','temperature'):raise ValueError('kind harus network atau temperature')
        metadata['kind']=kind
        enabled=item.get('enabled',True)
        if type(enabled) is not bool:raise ValueError('enabled harus true atau false')
        features=item.get('features')
        if features is not None:
            if not isinstance(features,dict) or set(features)-{'network','temperature'} or any(type(v) is not bool for v in features.values()):
                raise ValueError('features.network dan features.temperature harus true atau false')
            features=dict(network=features.get('network',kind=='network'),temperature=features.get('temperature',kind=='temperature'))
        metadata.update(enabled=enabled,features=features)
        key=item.get('device_key')
        if key is not None and (not isinstance(key,str) or not 8<=len(key)<=96 or any(ord(c)<33 or ord(c)>126 for c in key)):
            raise ValueError('device_key harus 8–96 karakter ASCII tanpa spasi')
        checked.append((device_id,floor,metadata,key))
    with connect() as db:
        for device_id,floor,m,key in checked:
            existing=db.device(device_id)
            if not existing:
                if key is None:raise ValueError('Perangkat baru '+device_id+' membutuhkan device_key')
                db.put_device(device_id,floor,hashlib.sha256(key.encode()).hexdigest(),**m)
            else:
                db.put_device(device_id,floor,**m)
            if key:
                db.add_key(device_id,hashlib.sha256(key.encode()).hexdigest())
    notify_update()
    return len(checked)


def reload_device_config():
    """Apply a changed config before serving dashboard APIs."""
    global CONFIG_WATCH_SIGNATURE
    if CONFIG_WATCH_PATH is None:return
    with CONFIG_RELOAD_LOCK:
        stat=CONFIG_WATCH_PATH.stat()
        signature=(stat.st_mtime_ns,stat.st_size)
        if signature==CONFIG_WATCH_SIGNATURE:return
        apply_device_config(CONFIG_WATCH_PATH)
        with OVERVIEW_LOCK:
            OVERVIEW_CACHE.update(until=0,data=None)
        CONFIG_WATCH_SIGNATURE=signature


def delivery_config(db, device_id, now=None):
    now=time.time() if now is None else now
    row=db.control(device_id)
    latest=db.latest(device_id)
    reported=json.loads(latest['details']).get('delivery',{}) if latest else {}
    interval=reported.get('telemetry_interval_s',10)
    remaining=max(0, min(120,row['live_until']-now)) if row else 0
    live_interval=row['live_interval_s'] if row else 2
    return dict(mode='live' if remaining>0 else 'telemetry', telemetry_interval_s=interval,
                live_interval_s=live_interval, send_interval_s=live_interval if remaining>0 else interval,
                live_remaining_s=int(remaining),revision=row['revision'] if row else 0,
                ping=dict(job_id=row['ping_id'],target=row['ping_target']) if row and row['ping_until']>now and ping_status(db,device_id,now)['status']=='queued' else None)


def ping_status(db,device_id,now=None):
    now=time.time() if now is None else now
    row=db.control(device_id)
    if not row or not row['ping_id']:return dict(status='idle')
    latest=db.latest(device_id)
    result=json.loads(latest['details']).get('ping') if latest else None
    if result and result['job_id']==row['ping_id'] and latest['received']>=row['ping_until']-60:
        return dict(status='completed',job_id=row['ping_id'],target=row['ping_target'],result=result)
    return dict(status='queued' if now<row['ping_until'] else 'expired',job_id=row['ping_id'],target=row['ping_target'])

def request_ping(device_id,payload):
    if not isinstance(payload,dict):raise ValueError('Body harus objek JSON')
    try:ip=ipaddress.IPv4Address(payload.get('target',''))
    except (ValueError,TypeError):raise ValueError('Tujuan harus IPv4 yang valid')
    if ip.is_multicast or ip.is_unspecified or ip.is_loopback or int(ip)>>24>=224 or int(ip)&255==255:raise ValueError('Tujuan harus IPv4 unicast')
    with connect() as db:
        device=db.device(device_id)
        if not device:raise LookupError('Perangkat tidak ditemukan')
        latest=db.latest(device_id)
        details=json.loads(latest['details']) if latest else {}
        if device['kind']!='network' or details.get('features',{}).get('network') is False:raise ValueError('Pemantauan jaringan perangkat harus aktif')
        if not latest or time.time()-latest['received']>max(35,details.get('delivery',{}).get('send_interval_s',10)*3+5):raise ValueError('Perangkat belum online')
        if details.get('wifi',{}).get('ip')==str(ip):raise ValueError('Tujuan tidak boleh IP perangkat sendiri')
        if ping_status(db,device_id)['status']=='queued':raise ValueError('Masih ada permintaan ping; tunggu hasil atau kedaluwarsa')
        job=secrets.randbelow(2147483646)+1
        db.put_control(device_id,ping_id=job,ping_target=str(ip),ping_until=time.time()+60)
        result=ping_status(db,device_id)
    notify_update()
    return result


def authenticate_device(db, device_id, key):
    row=db.device(device_id)
    digest=hashlib.sha256(key.encode()).hexdigest()
    return bool(row and (hmac.compare_digest(row['key_hash'],digest) or db.has_key(device_id,digest)))


def update_delivery(device_id, payload):
    if not isinstance(payload,dict):raise ValueError('Body harus objek JSON')
    mode=payload.get('mode')
    if mode not in ('live','telemetry'):raise ValueError('mode harus live atau telemetry')
    if 'telemetry_interval_s' in payload:
        raise ValueError('Interval Telemetri diatur di /setup ESP, bukan dashboard')
    interval=payload.get('live_interval_s',2)
    if type(interval) is not int or not 2<=interval<=30:raise ValueError('Interval Live harus 2–30 detik')
    duration=payload.get('live_duration_s',120)
    if type(duration) is not int or not 1<=duration<=120:raise ValueError('Durasi Live maksimal 120 detik')
    with connect() as db:
        device=db.device(device_id)
        if not device:raise LookupError('Perangkat tidak ditemukan')
        if device['kind']!='network':raise ValueError('Kontrol Live hanya untuk perangkat jaringan')
        until=time.time()+duration if mode=='live' else 0
        db.put_control(device_id,live_interval_s=interval,live_until=until,revision=(db.control(device_id) or {}).get('revision',0)+1)
        result=delivery_config(db,device_id)
    notify_update()
    return result


def duration(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 120000:
        raise ValueError('Durasi harus angka 0–120000 ms atau null')
    return value


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('Body harus objek JSON')
    if type(payload.get('rssi')) is not int or not -120 <= payload['rssi'] <= 0:
        raise ValueError('rssi harus integer -120 sampai 0')
    local = duration(payload.get('local_ms'))
    ok = payload.get('internet_ok')
    if ok is not None and type(ok) is not bool:
        raise ValueError('internet_ok harus boolean atau null')
    ms = duration(payload.get('internet_ms'))
    if ok is True and ms is None:
        raise ValueError('internet_ms wajib untuk pengujian berhasil')
    if ok is not True and ms is not None:
        raise ValueError('internet_ms harus null jika gagal atau belum diuji')
    return payload['rssi'], local, ok, ms


# Optional v2 metadata. Whitelist fields so credentials never reach read APIs.
DETAIL_SCHEMA = {
    'delivery': {'mode':str,'send_interval_s':int,'telemetry_interval_s':int,'revision':int},
    'firmware_version': str, 'boot_id': str, 'sequence': int,
    'wifi': {'ssid': str, 'bssid': str, 'channel': int, 'ip': str, 'gateway': str,
             'dns': str, 'disconnect_count': int, 'disconnected_ms': int,
             'last_disconnect_reason': int},
    'device_health': {'uptime_s': int, 'free_heap_bytes': int,
                      'heap_fragmentation_percent': int, 'reset_reason': str},
    'diagnostics': {'local_http_code': int, 'internet_http_code': int, 'internet_test_kind':str,'service_target':str},
    'ping': {'job_id': int, 'target': str, 'sent': int, 'received': int,
             'loss_percent': float, 'min_ms': float, 'avg_ms': float,
             'max_ms': float, 'age_ms': int, 'packets':[{'seq':int,'bytes':int,'ms':int,'ok':bool}]}
}


SERVICE_TARGETS={'google':'www.google.com','youtube':'www.youtube.com','tiktok':'www.tiktok.com','netflix':'www.netflix.com'}
DETAIL_SCHEMA['gateway_ping']={k:v for k,v in DETAIL_SCHEMA['ping'].items() if k not in ('job_id','packets')}
DETAIL_SCHEMA['gateway_ping']['measurement_id']=int
DETAIL_SCHEMA['features']={'network':bool,'temperature':bool}
DETAIL_SCHEMA['environment']={'sensor_ok':bool,'temperature_c':float,'humidity_percent':float,'reading_age_ms':int}
DETAIL_SCHEMA['services']={name:{'dns_ok':bool,'tcp_ok':bool,'dns_ms':int,'tcp_ms':int,'age_ms':int} for name in SERVICE_TARGETS}

def validate_details(payload):
    def clean(value, schema, path=''):
        if not isinstance(value, dict):
            raise ValueError(path+' harus objek')
        out = {}
        for key, expected in schema.items():
            if key not in value:
                continue
            v = value[key]
            if v is None:
                if path == 'environment.' and key in ('temperature_c','humidity_percent'):
                    out[key]=None
                    continue
                if path.startswith('services.') and key == 'tcp_ms':
                    out[key] = None
                    continue
                if path in ('ping.','gateway_ping.') and key in ('min_ms','avg_ms','max_ms'):
                    out[key] = None
                    continue
                if path == 'diagnostics.' and key == 'internet_http_code':
                    out[key] = None
                    continue
                raise ValueError(path+key+' tidak boleh null')
            if isinstance(expected,list):
                if not isinstance(v,list) or len(v)>4:raise ValueError('Maksimal empat hasil paket ping')
                out[key]=[clean(packet,expected[0],path+key+'.') for packet in v]
                if any(set(packet)!=set(expected[0]) for packet in out[key]):raise ValueError('Hasil paket ping tidak lengkap')
            elif isinstance(expected, dict):
                out[key] = clean(v, expected, path+key+'.')
            elif expected is bool:
                if type(v) is not bool:raise ValueError(path+key+' harus boolean')
                out[key]=v
            elif expected is str:
                if not isinstance(v, str) or len(v)>128 or any(ord(c)<32 for c in v):
                    raise ValueError(path+key+' harus teks maksimal 128 karakter tanpa kontrol')
                out[key] = v
            else:
                valid = type(v) is int if expected is int else type(v) in (int,float)
                if not valid or not math.isfinite(v) or abs(v)>2**53-1:
                    raise ValueError(path+key+' harus angka valid')
                if v<0 and key not in ('local_http_code','internet_http_code','temperature_c'):
                    raise ValueError(path+key+' tidak boleh negatif')
                if key in ('loss_percent','heap_fragmentation_percent') and v>100:
                    raise ValueError(path+key+' maksimal 100')
                if key=='channel' and not 1<=v<=14:
                    raise ValueError('channel harus 1–14')
                out[key] = v
        return out
    details = clean(payload, DETAIL_SCHEMA)
    environment=details.get('environment')
    if environment is not None:
        if set(environment)!={'sensor_ok','temperature_c','humidity_percent','reading_age_ms'}:raise ValueError('Data sensor suhu tidak lengkap')
        t,h=environment['temperature_c'],environment['humidity_percent']
        if environment['sensor_ok']:
            if t is None or h is None or not -40<=t<=80 or not 0<=h<=100:raise ValueError('Nilai DHT21 di luar rentang')
        elif t is not None or h is not None:raise ValueError('Sensor gagal harus mengirim suhu/kelembapan null')
    delivery=details.get('delivery')
    if delivery is not None:
        if delivery.get('mode') not in ('live','telemetry') or type(delivery.get('send_interval_s')) is not int or not 2<=delivery['send_interval_s']<=3600 or type(delivery.get('telemetry_interval_s')) is not int or not 10<=delivery['telemetry_interval_s']<=3600:
            raise ValueError('Laporan mode pengiriman tidak valid')
    for field in ('ping','gateway_ping'):
        ping=details.get(field)
        if ping is None:continue
        if not {('job_id' if field=='ping' else 'measurement_id'),'target','sent','received','loss_percent','min_ms','avg_ms','max_ms','age_ms'} <= ping.keys():
            raise ValueError('Data ping tidak lengkap')
        if not 1<=ping['sent']<=100 or ping['received']>ping['sent']:
            raise ValueError('Jumlah paket ping tidak valid')
        if field=='gateway_ping':
            try:target=ipaddress.IPv4Address(ping['target'])
            except (ValueError,TypeError):raise ValueError('Gateway harus IPv4')
            if target.is_unspecified or target.is_loopback or target.is_multicast:raise ValueError('Gateway tidak valid')
            configured=details.get('wifi',{}).get('gateway')
            if configured and str(target)!=configured:raise ValueError('Target ping harus sama dengan gateway Wi-Fi')
        expected_loss=100*(ping['sent']-ping['received'])/ping['sent']
        if abs(ping['loss_percent']-expected_loss)>.11:
            raise ValueError('Packet loss tidak sesuai jumlah paket')
        rtts=[ping[k] for k in ('min_ms','avg_ms','max_ms')]
        if (ping['received']==0 and any(v is not None for v in rtts)) or (ping['received']>0 and (any(v is None for v in rtts) or not rtts[0]<=rtts[1]<=rtts[2])):
            raise ValueError('RTT ping tidak valid')
    for name,result in details.get('services',{}).items():
        if set(result)!={'dns_ok','tcp_ok','dns_ms','tcp_ms','age_ms'}:
            raise ValueError('Hasil layanan '+name+' tidak lengkap')
        if not result['dns_ok'] and (result['tcp_ok'] or result['tcp_ms'] is not None):
            raise ValueError('TCP tidak boleh berhasil jika DNS gagal')
        if result['dns_ok'] and result['tcp_ms'] is None:
            raise ValueError('Durasi TCP wajib jika DNS berhasil')
        if result['dns_ms']>120000 or (result['tcp_ms'] is not None and result['tcp_ms']>120000):
            raise ValueError('Durasi uji layanan maksimal 120000 ms')
    return details


def snapshot(now=None, device_id=None):
    now = time.time() if now is None else now
    floors = []
    with connect() as db:
        devices = [db.device(device_id)] if device_id and db.device(device_id) else [] if device_id else db.devices(kind='network')
        for device in devices:
            if not device.get('enabled',True) or device.get('features') is not None and not device['features']['network']:continue
            latest = db.latest(device['id'])
            if device.get('features') is None and latest and json.loads(latest['details']).get('features',{}).get('network') is False:continue
            rows = db.history(device['id'],since=now-300)
            node = dict(id=device['id'], latestInternetOk=bool(latest['internet_ok']) if latest and latest['internet_ok'] is not None else None, latestInternetMs=latest['internet_ms'] if latest else None, details=json.loads(latest['details']) if latest else {}, floorId=device['floor'], name=device['name'] or device['id'], building=device['building'], position=device['room'], online=bool(latest and now-latest['received'] <= 35), lastSeen=latest['received']*1000 if latest else None, rssi=latest['rssi'] if latest else None, latency=latest['local_ms'] if latest else None, internet=[dict(time=r['received']*1000, ok=bool(r['internet_ok']), ms=r['internet_ms']) for r in rows if r['internet_ok'] is not None])
            desired=delivery_config(db,device['id'],now)
            reported=node['details'].get('delivery',{})
            expected=reported.get('send_interval_s',10)
            node['offline_after_s']=max(35,expected*3+5)
            node['online']=bool(latest and now-latest['received']<=node['offline_after_s'])
            node['control']=desired
            node['services']={}
            for name,host in SERVICE_TARGETS.items():
                sample=node['details'].get('services',{}).get(name)
                age=(sample['age_ms']+max(0,now-latest['received'])*1000) if sample else None
                fresh=bool(node['online'] and sample and age<=max(90000,expected*5000))
                status='unknown' if not sample else 'stale' if not fresh else 'reachable' if sample['tcp_ok'] else 'dns_failed' if not sample['dns_ok'] else 'tcp_failed'
                node['services'][name]=dict(host=host,status=status,age_ms=age,**({k:v for k,v in sample.items() if k!='age_ms'} if sample else {}))

            # Older firmware repeats cached results; reconstruct measurement time
            # and collapse repeats with the same values and small transport jitter.
            node['service_history']={name:[] for name in SERVICE_TARGETS}
            previous={}
            for row in rows:
                detail=json.loads(row['details'])
                for name,result in detail.get('services',{}).items():
                    measured=row['received']*1000-result['age_ms']
                    if measured<(now-300)*1000 or measured>now*1000:continue
                    signature=(detail.get('boot_id'),result['dns_ok'],result['tcp_ok'],result['dns_ms'],result['tcp_ms'])
                    last=previous.get(name)
                    if last and last[0]==signature and abs(measured-last[1])<=2000:continue
                    previous[name]=(signature,measured)
                    node['service_history'][name].append(dict(time=measured,dns_ok=result['dns_ok'],tcp_ok=result['tcp_ok'],dns_ms=result['dns_ms'],tcp_ms=result['tcp_ms'],max_gap_ms=max(90000,detail.get('delivery',{}).get('send_interval_s',10)*5000)))
            for history in node['service_history'].values():history.sort(key=lambda sample:sample['time'])
            node['gateway_history']=[]
            previous_gateway=None
            for row in rows:
                detail=json.loads(row['details']);g=detail.get('gateway_ping')
                if not g:continue
                measured=row['received']*1000-g['age_ms']
                if not (now-300)*1000<=measured<=now*1000:continue
                signature=(detail.get('boot_id'),g['measurement_id'],g['target'])
                if previous_gateway and signature==previous_gateway[0] and abs(measured-previous_gateway[1])<=2000:continue
                previous_gateway=(signature,measured)
                node['gateway_history'].append(dict(time=measured,value=g['avg_ms'] if g['received'] else None,target=g['target'],loss_percent=g['loss_percent'],sent=g['sent'],received=g['received'],max_gap_ms=max(35000,detail.get('delivery',{}).get('send_interval_s',10)*1500)))
            node['history']=[dict(time=r['received']*1000,value=r['local_ms'],max_gap_ms=max(35000,json.loads(r['details']).get('delivery',{}).get('send_interval_s',10)*1500)) for r in rows]
            group=next((f for f in floors if f['id']==device['floor'] and f['building']==device['building']),None)
            if group is None:
                group=dict(id=device['floor'],name=f"Lantai {device['floor']}",building=device['building'],area=device['building'],nodes=[],history=[])
                floors.append(group)
            group['nodes'].append(node)
        for floor in floors:
            buckets={}
            for node in floor['nodes']:
                for sample in node['history']:
                    buckets.setdefault(int(sample['time']//10000)*10000,[]).append(sample['value'])
            floor['history']=[dict(time=t,value=sum(v for v in values if v is not None)/len([v for v in values if v is not None]) if any(v is not None for v in values) else None) for t,values in sorted(buckets.items())]
    return dict(serverTime=now*1000, floors=floors)


def environment_snapshot(now=None):
    now=time.time() if now is None else now
    result=[]
    with connect() as db:
        for device in [{k:d[k] for k in ('id','name','building','floor','room','kind')} | {'features':d.get('features')} for d in db.devices() if d.get('enabled',True) and (d.get('features') is None or d['features']['temperature'])]:
            latest=db.latest(device['id'])
            latest_details=json.loads(latest['details']) if latest else {}
            if device.get('features') is None and device['kind']!='temperature' and not latest_details.get('features',{}).get('temperature',False) and 'environment' not in latest_details:continue
            if device.get('features') is None and latest_details.get('features',{}).get('temperature') is False:continue
            readings={};failed=0
            for row in db.history(device['id'],since=now-86400):
                env=json.loads(row['details']).get('environment')
                if not env:continue
                if not env['sensor_ok']:failed+=1;continue
                bucket=int(row['received']//3600)*3600
                values=readings.setdefault(bucket,{'t':[],'h':[]})
                values['t'].append(env['temperature_c']);values['h'].append(env['humidity_percent'])
            details=json.loads(latest['details']) if latest else {}
            env=details.get('environment')
            offline_after=max(95,details.get('delivery',{}).get('send_interval_s',30)*3+5)
            online=bool(latest and now-latest['received']<=offline_after)
            age=env['reading_age_ms']+max(0,now-latest['received'])*1000 if env else None
            status='waiting' if not latest else 'offline' if not online else 'sensor_error' if not env or not env['sensor_ok'] else 'stale' if age>offline_after*1000 else 'ok'
            t=[v for values in readings.values() for v in values['t']];h=[v for values in readings.values() for v in values['h']]
            result.append(dict(**dict(device),status=status,online=online,ip=details.get('wifi',{}).get('ip'),last_seen_ms=latest['received']*1000 if latest else None,reading_age_ms=age,temperature_c=env['temperature_c'] if status=='ok' else None,humidity_percent=env['humidity_percent'] if status=='ok' else None,
                stats_24h=dict(samples=len(t),failed=failed,min_c=min(t) if t else None,max_c=max(t) if t else None,average_c=round(sum(t)/len(t),1) if t else None,average_humidity=round(sum(h)/len(h),1) if h else None),
                hourly=[dict(time=bucket*1000,temperature_c=round(sum(v['t'])/len(v['t']),1),humidity_percent=round(sum(v['h'])/len(v['h']),1),min_c=min(v['t']),max_c=max(v['t']),samples=len(v['t'])) for bucket,v in sorted(readings.items())]))
    return dict(server_time_ms=now*1000,from_ms=(now-86400)*1000,devices=result)


OVERVIEW_LOCK=threading.Lock()
OVERVIEW_CACHE={'until':0,'data':None}


def summary_24h(now=None, start=None, device_id=None):
    now=time.time() if now is None else now
    start=now-86400 if start is None else start
    def stats():return {'tests':0,'failed':0,'values':Counter()}
    def add(target,value,ok=True):
        target['tests']+=1
        if ok and value is not None:target['values'][round(value,1)]+=1
        else:target['failed']+=1
    def finish(target):
        count=sum(target['values'].values())
        average=round(sum(v*n for v,n in target['values'].items())/count,1) if count else None
        rank=math.ceil(count*.95);seen=0;p95=None
        for value,n in sorted(target['values'].items()):
            seen+=n
            if seen>=rank:p95=value;break
        return dict(tests=target['tests'],failed=target['failed'],average_ms=average,p95_ms=p95,success_percent=round(100*count/target['tests'],1) if target['tests'] else None)
    def serviceStats():return {metric:stats() for metric in ('total','dns','tcp')}
    services={name:serviceStats() for name in SERVICE_TARGETS};local=stats();gateway=stats();hours={};previous={}
    def hour(t):
        key=int(t//3600)*3600
        if key not in hours:hours[key]={'local':stats(),'gateway':stats(),'services':{name:serviceStats() for name in SERVICE_TARGETS}}
        return hours[key]
    with connect() as db:
        rows=db.history(device_id,since=start) if device_id else db.network_history(start)
        for row in rows:
            if row['received']>now:continue
            if json.loads(row['details']).get('features',{}).get('network') is False:continue
            h=hour(row['received']);add(local,row['local_ms'],row['local_ms'] is not None);add(h['local'],row['local_ms'],row['local_ms'] is not None)
            detail=json.loads(row['details'])
            g=detail.get('gateway_ping')
            if g:
                measured=row['received']-g['age_ms']/1000
                signature=(detail.get('boot_id'),g['measurement_id'],g['target'])
                key=(row['device_id'],'gateway_ping');last=previous.get(key)
                if start<=measured<=now and not (last and signature==last[0] and abs(measured-last[1])<=2):
                    previous[key]=(signature,measured)
                    add(gateway,g['avg_ms'],g['received']>0);add(hour(measured)['gateway'],g['avg_ms'],g['received']>0)
            for name,result in detail.get('services',{}).items():
                measured=row['received']-result['age_ms']/1000
                if not start<=measured<=now:continue
                signature=(detail.get('boot_id'),result['dns_ok'],result['tcp_ok'],result['dns_ms'],result['tcp_ms'])
                key=(row['device_id'],name);last=previous.get(key)
                if last and signature==last[0] and abs(measured-last[1])<=2:continue
                previous[key]=(signature,measured)
                h=hour(measured)
                for metric,value,ok in [('dns',result['dns_ms'],result['dns_ok']),('tcp',result['tcp_ms'],result['tcp_ok']),('total',result['dns_ms']+(result['tcp_ms'] or 0),result['tcp_ok'])]:
                    add(services[name][metric],value,ok);add(h['services'][name][metric],value,ok)
    hourly=[]
    for timestamp,h in sorted(hours.items()):
        hourly.append(dict(time=timestamp*1000,local=finish(h['local']),gateway=finish(h['gateway']),services={name:{metric:finish(st) for metric,st in metrics.items()} for name,metrics in h['services'].items()}))
    def peak(items):
        available=[(timestamp,st) for timestamp,st in items if st['average_ms'] is not None]
        if not available:return None
        timestamp,st=max(available,key=lambda item:item[1]['average_ms'])
        return dict(hour_start_ms=timestamp,average_ms=st['average_ms'],tests=st['tests'])
    result=dict(from_ms=start*1000,to_ms=now*1000,bucket_seconds=3600,local=finish(local),gateway=finish(gateway),services={name:{metric:finish(st) for metric,st in metrics.items()} for name,metrics in services.items()},hourly=hourly)
    result['slowest_hours']={'gateway':peak([(h['time'],h['gateway']) for h in hourly]),'local':peak([(h['time'],h['local']) for h in hourly]),'services':{name:peak([(h['time'],h['services'][name]['total']) for h in hourly]) for name in SERVICE_TARGETS}}
    return result


def overview():
    now=time.time()
    with OVERVIEW_LOCK:
        if OVERVIEW_CACHE['data'] is not None and now<OVERVIEW_CACHE['until']:return OVERVIEW_CACHE['data']
        data=snapshot(now)
        data['summary_24h']=summary_24h(now)
        for floor in data['floors']:
            for node in floor['nodes']:
                report=summary_24h(now,device_id=node['id'])
                report.pop('hourly',None)
                node['summary_24h']=report
                node['history']=[];node['service_history']={}
            floor['history']=[]
        OVERVIEW_CACHE.update(until=now+300,data=data)
        return data


def device_day(device_id,date):
    try:
        day=datetime.strptime(date,'%Y-%m-%d')
        if day.strftime('%Y-%m-%d')!=date:raise ValueError()
    except (ValueError,TypeError):raise ValueError('Tanggal harus YYYY-MM-DD')
    with connect() as store:
        device=store.device(device_id)
        if not device:raise LookupError('Perangkat tidak ditemukan')
        start=day.replace(tzinfo=store.timezone).timestamp()
        if start>time.time():raise ValueError('Tanggal tidak boleh di masa depan')
        end=(day+timedelta(days=1)).replace(tzinfo=store.timezone).timestamp()
        cutoff=min(end-0.000001,time.time())
        rows=[r for r in store.history(device_id,since=start) if r['received']<=cutoff]
    latest=max(rows,key=lambda r:r['id']) if rows else None
    details=json.loads(latest['details']) if latest else {}
    node=dict(id=device_id,floorId=device['floor'],name=device['name'] or device_id,building=device['building'],position=device['room'],online=False,lastSeen=latest['received']*1000 if latest else None,rssi=latest['rssi'] if latest else None,latency=latest['local_ms'] if latest else None,latestInternetOk=bool(latest['internet_ok']) if latest and latest['internet_ok'] is not None else None,latestInternetMs=latest['internet_ms'] if latest else None,details=details,internet=[],history=[],services={},service_history={},control={'mode':'telemetry','live_remaining_s':0},offline_after_s=35)
    floor=dict(id=device['floor'],name='Lantai '+str(device['floor']),building=device['building'],area=device['building'],nodes=[node],history=[])
    summary=summary_24h(cutoff,start=start,device_id=device_id)
    summary['to_ms']=end*1000
    return dict(serverTime=time.time()*1000,floors=[floor],view='history',date=date,timezone='Asia/Jakarta',samples=len(rows),summary_24h=summary)


def quality(node):
    tests = node['internet'] if node['online'] else []
    if not tests:
        return dict(status='unknown', success_percent=None, average_ms=None, tests=0)
    good = [t for t in tests if t['ok']]
    rate = len(good)/len(tests)*100
    ms = sum(t['ms'] for t in good)/len(good) if good else None
    status = 'unreachable' if not good else 'partial' if rate < 95 else 'slow' if ms > 800 else 'normal'
    return dict(status=status, success_percent=round(rate, 1), average_ms=round(ms, 1) if ms is not None else None, tests=len(tests))


def device_list():
    data = snapshot()
    devices = []
    for floor in data['floors']:
        for node in floor['nodes']:
            devices.append(dict(id=node['id'],kind='network', floor=floor['id'],name=node['name'],building=node['building'],room=node['position'], status='waiting' if node['lastSeen'] is None else 'online' if node['online'] else 'offline', last_seen_ms=node['lastSeen'], rssi=node['rssi'] if node['online'] else None, local_ms=node['latency'] if node['online'] else None, internet=quality(node), details=node['details'],services=node['services'],control=node['control'],offline_after_s=node['offline_after_s']))
    for sensor in environment_snapshot()['devices']:
        existing=next((d for d in devices if d['id']==sensor['id']),None)
        if existing is not None:
            existing['environment']=dict(temperature_c=sensor['temperature_c'],humidity_percent=sensor['humidity_percent'],status=sensor['status'])
            existing['stats_24h']=sensor['stats_24h']
            continue
        devices.append(dict(id=sensor['id'],floor=sensor['floor'],name=sensor['name'],building=sensor['building'],room=sensor['room'],kind='temperature',status=sensor['status'],last_seen_ms=sensor['last_seen_ms'],environment=dict(temperature_c=sensor['temperature_c'],humidity_percent=sensor['humidity_percent']),stats_24h=sensor['stats_24h']))
    return dict(server_time_ms=data['serverTime'], devices=devices)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        origin = self.headers.get('Origin')
        if origin and origin in os.environ.get('NETPULSE_CORS_ORIGINS', '').split(','):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def stream_dashboard(self, device_id=None):
        # The same LAN visibility as /api/dashboard; no keys are included.
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache, no-transform')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Connection', 'close')
        self.end_headers()
        previous=None
        try:
            while True:
                with UPDATES:
                    revision = REVISION
                data=snapshot(device_id=device_id)
                comparable=json.loads(json.dumps(data))
                comparable.pop('serverTime',None)
                for floor in comparable['floors']:
                    for node in floor['nodes']:
                        node.get('control',{}).pop('live_remaining_s',None)
                        for service in node.get('services',{}).values():service.pop('age_ms',None)
                signature=json.dumps(comparable,sort_keys=True)
                if device_id and signature==previous:
                    self.wfile.write(b': keep-alive\n\n')
                else:
                    encoded=json.dumps(data,allow_nan=False,separators=(',',':'))
                    self.wfile.write(('event: dashboard\ndata: '+encoded+'\n\n').encode())
                previous=signature
                self.wfile.flush()
                # Periodic snapshots also expire stale nodes/tests without a POST.
                with UPDATES:
                    UPDATES.wait_for(lambda: REVISION != revision, timeout=5)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        self.close_connection = True

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if path.startswith('/controllers/'):
            try:reload_device_config()
            except (ValueError,OSError):return self.reply(400,dict(error='Konfigurasi perangkat tidak valid'))
            return controller_proxy.serve(self,connect)
        if path.startswith('/api/') or path.startswith('/devices/') or path.startswith('/temperature/devices/'):
            try:reload_device_config()
            except (ValueError,OSError) as exc:return self.reply(400,dict(error='Konfigurasi perangkat tidak valid: '+str(exc)))
        if path == '/api/device/control':
            device_id=self.headers.get('X-Device-ID','')
            with connect() as db:
                if not authenticate_device(db,device_id,self.headers.get('X-Device-Key','')):
                    return self.reply(401,dict(error='Kredensial perangkat salah'))
                return self.reply(200,delivery_config(db,device_id))
        if path.startswith('/api/v1/'):
            token = os.environ.get('NETPULSE_READ_TOKEN', '')
            if token and not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token):
                return self.reply(401, dict(error='Bearer token salah atau belum diisi'))
            if path == '/api/v1/devices':
                return self.reply(200, device_list())
            parts = path.strip('/').split('/')
            if len(parts) == 5 and parts[:3] == ['api', 'v1', 'devices']:
                device_id, action = unquote(parts[3]), parts[4]
                with connect() as db:
                    if not db.device(device_id):
                        return self.reply(404, dict(error='Perangkat tidak ditemukan'))
                    if action == 'ping':
                        with connect() as db:return self.reply(200,ping_status(db,device_id))
                    if action == 'control':
                        return self.reply(200,delivery_config(db,device_id))
                    if action == 'latest':
                        result = next(d for d in device_list()['devices'] if d['id'] == device_id)
                        return self.reply(200, result)
                    if action == 'history':
                        query = parse_qs(parsed.query)
                        try:
                            after = int(query.get('after_id', ['0'])[0])
                            limit = int(query.get('limit', ['100'])[0])
                            if after < 0 or not 1 <= limit <= 1000:
                                raise ValueError()
                        except ValueError:
                            return self.reply(400, dict(error='after_id >= 0 dan limit 1–1000'))
                        rows = db.history(device_id,after_id=after,limit=limit+1)
                        has_more = len(rows) > limit
                        items = [dict(id=r['id'], device_id=r['device_id'], received_at_ms=r['received']*1000, rssi=r['rssi'], local_ms=r['local_ms'], internet_ok=bool(r['internet_ok']) if r['internet_ok'] is not None else None, internet_ms=r['internet_ms'], details=json.loads(r['details'])) for r in rows[:limit]]
                        return self.reply(200, dict(items=items, next_after_id=items[-1]['id'] if items else after, has_more=has_more))
            return self.reply(404, dict(error='Endpoint tidak ditemukan'))
        if path in ('/api/events','/api/dashboard'):
            device_id=parse_qs(parsed.query).get('device_id',[None])[0]
            if device_id:
                with connect() as store:
                    if not store.device(device_id):return self.reply(404,dict(error='Perangkat tidak ditemukan'))
            date=parse_qs(parsed.query).get('date',[None])[0]
            if date:
                if path!='/api/dashboard' or not device_id:return self.reply(400,dict(error='Riwayat tanggal memerlukan device_id pada /api/dashboard'))
                try:return self.reply(200,device_day(device_id,date))
                except ValueError as exc:return self.reply(400,dict(error=str(exc)))
                except LookupError as exc:return self.reply(404,dict(error=str(exc)))
            if path=='/api/events':return self.stream_dashboard(device_id)
            return self.reply(200,snapshot(device_id=device_id))
        if path == '/api/environment':
            return self.reply(200,environment_snapshot())
        if path == '/api/overview':
            return self.reply(200,overview())
        if path == '/api/dashboard':
            return self.reply(200, snapshot())
        if path == '/api/probe':
            return self.reply(200, dict(status='ok', service='netpulse'))
        if path == '/api/openapi.json':
            return self.reply(200, (ROOT / 'docs' / 'openapi.json').read_bytes())
        if path.startswith('/temperature/devices/'):
            device_id=unquote(path[len('/temperature/devices/'):].rstrip('/'))
            if not device_id or '/' in device_id:
                return self.reply(404,dict(error='Perangkat tidak ditemukan'))
            with connect() as db:
                device=db.device(device_id)
            if not device or not device.get('enabled',True) or (device.get('features') is not None and not device['features']['temperature']):
                return self.reply(404,dict(error='Perangkat suhu tidak ditemukan atau tidak aktif'))
            return self.reply(200,(ROOT/'temperature-device.html').read_text().replace('id="controller-access"', 'id="controller-access" href="/controllers/'+device_id+'/"').encode(),'text/html; charset=utf-8')
        if path.startswith('/devices/'):
            device_id=unquote(path[len('/devices/'):].rstrip('/'))
            if not device_id or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in device_id):
                return self.reply(404,dict(error='Perangkat tidak ditemukan'))
            with connect() as db:
                device=db.device(device_id)
                if not device:return self.reply(404,dict(error='Perangkat tidak ditemukan'))
            filename='temperature-device.html' if device['kind']=='temperature' else 'device.html'
            return self.reply(200,(ROOT/filename).read_text().replace('id="controller-access"', 'id="controller-access" href="/controllers/'+device_id+'/"').encode(),'text/html; charset=utf-8')
        files = {'/assets/chart-tooltips.js':('assets/chart-tooltips.js','application/javascript; charset=utf-8'), '/temperature':('temperature.html','text/html; charset=utf-8'), '/assets/temperature.js':('assets/temperature.js','application/javascript; charset=utf-8'), '/assets/dashboard.css': ('assets/dashboard.css','text/css; charset=utf-8'), '/assets/dashboard.js': ('assets/dashboard.js','application/javascript; charset=utf-8'), '/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'), '/logo.png': ('logo.png', 'image/png')}
        if path in files:
            filename, mime = files[path]
            return self.reply(200, (ROOT / filename).read_bytes(), mime)
        self.reply(404, dict(error='Tidak ditemukan'))

    def do_OPTIONS(self):
        self.reply(204, b'')

    def do_POST(self):
        if urlsplit(self.path).path.startswith('/controllers/'):
            return controller_proxy.serve(self,connect)
        parts=urlsplit(self.path).path.strip('/').split('/')
        if len(parts)==5 and parts[:3]==['api','v1','devices'] and parts[4] in ('control','ping'):
            token=os.environ.get('NETPULSE_READ_TOKEN','')
            if token and not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                return self.reply(401,dict(error='Bearer token salah atau belum diisi'))
            origin=self.headers.get('Origin')
            allowed=os.environ.get('NETPULSE_CORS_ORIGINS','').split(',')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host','') and origin not in allowed:
                return self.reply(403,dict(error='Origin tidak diizinkan'))
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                return self.reply(415,dict(error='Content-Type harus application/json'))
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024:raise ValueError('Ukuran body harus 1–1024 byte')
                result=(request_ping if parts[4]=='ping' else update_delivery)(unquote(parts[3]),json.loads(self.rfile.read(size)))
            except (ValueError,UnicodeError) as exc:
                return self.reply(400,dict(error=str(exc)))
            except LookupError as exc:
                return self.reply(404,dict(error=str(exc)))
            return self.reply(202 if parts[4]=='ping' else 200,result)
        if self.path != '/api/telemetry':
            return self.reply(404, dict(error='Tidak ditemukan'))
        device_id = self.headers.get('X-Device-ID', '')
        key = self.headers.get('X-Device-Key', '')
        with connect() as db:
            device = db.device(device_id)
            digest = hashlib.sha256(key.encode()).hexdigest()
            primary_ok = bool(device and hmac.compare_digest(device['key_hash'], digest))
            additional_ok = bool(device and db.has_key(device_id,digest))
            if not (primary_ok or additional_ok):
                return self.reply(401, dict(error='Kredensial perangkat salah'))
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    return self.reply(413, dict(error='Ukuran body harus 1–4096 byte'))
                payload = json.loads(self.rfile.read(size))
                values = validate(payload)
                details = validate_details(payload)
            except (ValueError, UnicodeError) as exc:
                return self.reply(400, dict(error=str(exc)))
            now = time.time()
            try:
                db.append(device_id,now,*values,details=details)
            except (OSError,ValueError):
                self.log_error('Penyimpanan telemetri gagal untuk %s',device_id)
                return self.reply(503,dict(error='Telemetri belum tersimpan; periksa penyimpanan server'))
        notify_update()
        self.reply(201, dict(status='accepted', received_at=now))

    def setup(self):
        super().setup()
        self.connection.settimeout(10)


def main():
    global CONFIG_WATCH_PATH, CONFIG_WATCH_SIGNATURE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG, help='File JSON penempatan perangkat')
    parser.add_argument('--check-config', action='store_true', help='Validasi dan terapkan JSON, lalu keluar')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--register-floor', type=int)
    parser.add_argument('--issue-setup-floor', type=int, help='Buat kunci tambahan dan file isian ESP untuk lantai terdaftar')
    parser.add_argument('--server-url', help='Alamat HTTP LAN server untuk file setup')
    args = parser.parse_args()
    init_storage()
    if args.issue_setup_floor is not None:
        url = urlsplit(args.server_url or '')
        if url.scheme != 'http' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            parser.error('--server-url harus alamat HTTP server, misalnya http://192.168.0.52:8000')
        with connect() as db:
            device = next(iter(db.devices(floor=args.issue_setup_floor)),None)
            if not device:
                parser.error('Lantai belum terdaftar')
            key = secrets.token_urlsafe(32)
            db.add_key(device['id'],hashlib.sha256(key.encode()).hexdigest())
        path = Path(DATA_DIR) / ('setup-'+device['id']+'.txt')
        text = ('ISIAN ESP /setup — LANTAI '+str(args.issue_setup_floor)+'\n\n'
                'Login halaman setup: admin / waron114\n'
                'SSID Wi-Fi: isi nama Wi-Fi gedung yang digunakan ESP\n'
                'Password Wi-Fi: isi password Wi-Fi gedung, bukan password login setup\n\n'
                'Alamat backend HTTP: '+args.server_url.rstrip('/')+'\n'
                'ID perangkat: '+device['id']+'\n'
                'Kunci perangkat: '+key+'\n'
                'URL probe internet HTTP: kosongkan dulu\n\n'
                'Dashboard backend: '+args.server_url.rstrip('/')+'\n'
                'Probe internet harus endpoint HTTP eksternal dengan respons 204.\n'
                'Jika kosong, kualitas internet belum diketahui; data Wi-Fi/lokal tetap dikirim.\n'
                'Simpan file ini secara privat. Kunci lama tetap berlaku.\n')
        import stat
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(text)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        print('Isian siap salin: '+str(path))
        return
    if args.register_floor is not None:
        if not 1 <= args.register_floor <= 999:
            parser.error('Lantai harus 1–999')
        device_id = f'WMS-{args.register_floor:03d}'
        key = secrets.token_urlsafe(32)
        with connect() as db:
            if db.devices(floor=args.register_floor):
                parser.error('Lantai sudah terdaftar; kunci tidak diubah')
            db.put_device(device_id,args.register_floor,hashlib.sha256(key.encode()).hexdigest())
        print(json.dumps(dict(device_id=device_id, floor=args.register_floor, device_key=key)))
        return
    if args.config.exists():
        try:
            count=apply_device_config(args.config)
        except (ValueError, OSError) as exc:
            parser.error('Konfigurasi gagal: '+str(exc))
        CONFIG_WATCH_PATH=args.config.resolve()
        config_stat=CONFIG_WATCH_PATH.stat()
        CONFIG_WATCH_SIGNATURE=(config_stat.st_mtime_ns,config_stat.st_size)
        print(f'Konfigurasi diterapkan: {count} perangkat', flush=True)
    elif args.check_config:
        parser.error('File konfigurasi tidak ditemukan')
    if args.check_config:
        return
    print(f'WARON IoT tersedia di http://{args.host}:{args.port}', flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
