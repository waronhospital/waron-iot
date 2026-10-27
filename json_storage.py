"""Daily append-only JSONL telemetry with serialized local writers."""
import copy
import fcntl
import json
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from telemetry_rollups import Rollups


class JsonStorage:
    def __init__(self, root, timezone='Asia/Jakarta'):
        self.root = Path(root)
        self.timezone = ZoneInfo(timezone)
        self.lock = threading.RLock()
        self.local = threading.local()
        self.cache = OrderedDict()
        self.last_id = 0
        self.latest_cache = {}
        self.rollups=Rollups(self)

    @staticmethod
    def valid_id(device_id):
        if not isinstance(device_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',device_id):
            raise ValueError('ID perangkat tidak valid')
        return device_id

    def atomic_write(self, path, value):
        path.parent.mkdir(parents=True,exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as handle:
                json.dump(value,handle,ensure_ascii=False,allow_nan=False,indent=2)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary,path)
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)

    @contextmanager
    def session(self):
        with self.lock:
            if getattr(self.local,'active',False):
                yield self
                return
            self.root.mkdir(parents=True,exist_ok=True)
            with (self.root/'.storage.lock').open('a') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX)
                self.local.active=True
                try:
                    state=self.root/'state.json'
                    self.state=json.loads(state.read_text()) if state.exists() else dict(schema_version=1,devices={},keys={},controls={})
                    self.dirty=False
                    yield self
                    if self.dirty:self.atomic_write(state,self.state)
                finally:
                    self.local.active=False
                    fcntl.flock(handle,fcntl.LOCK_UN)

    def devices(self,kind=None,floor=None):
        rows=[copy.deepcopy(d) for d in self.state['devices'].values() if d.get('enabled',True) and (kind is None or (d.get('features') or {}).get(kind,d['kind']==kind)) and (floor is None or d['floor']==floor)]
        return sorted(rows,key=lambda d:(d['building'],d['floor'],d['room'],d['id']))

    def device(self,device_id):
        value=self.state['devices'].get(device_id)
        return copy.deepcopy(value) if value else None

    def put_device(self,device_id,floor,key_hash=None,**metadata):
        self.valid_id(device_id)
        device=self.device(device_id) or dict(id=device_id,floor=floor,key_hash=key_hash,name='',building='Gedung Utama',room='Titik pantau utama',kind='network')
        device.update(floor=floor,**metadata)
        if key_hash is not None:device['key_hash']=key_hash
        self.state['devices'][device_id]=device
        self.dirty=True

    def add_key(self,device_id,key_hash):
        keys=self.state['keys'].setdefault(device_id,[])
        if key_hash not in keys:keys.append(key_hash);self.dirty=True

    def has_key(self,device_id,key_hash):
        return key_hash in self.state['keys'].get(device_id,[])

    def control(self,device_id):
        value=self.state['controls'].get(device_id)
        return copy.deepcopy(value) if value else None

    def put_control(self,device_id,**fields):
        row=self.control(device_id) or dict(device_id=device_id,telemetry_interval_s=10,live_until=0,revision=0,live_interval_s=2,ping_id=0,ping_target='',ping_until=0)
        row.update(fields)
        self.state['controls'][device_id]=row
        self.dirty=True

    def paths(self,device_id,since=None):
        self.valid_id(device_id)
        cutoff=datetime.fromtimestamp(since,self.timezone).date() if since is not None else None
        result={}
        for extension in ('json','jsonl'):
            for path in (self.root/'telemetry').glob(device_id+'-*.'+extension):
                try:day=datetime.strptime(path.stem[len(device_id)+1:],'%d-%m-%Y').date()
                except ValueError:continue
                if cutoff is None or day>=cutoff:result[day]=path
        return [path for _,path in sorted(result.items())]

    def read_day(self,path):
        path=Path(path)
        stat=path.stat();signature=(stat.st_ino,stat.st_mtime_ns,stat.st_size)
        cached=self.cache.get(path)
        if cached and cached[0]==signature:
            self.cache.move_to_end(path)
            return cached[1]
        try:
            if path.suffix=='.jsonl':
                incremental=cached and cached[0][0]==stat.st_ino and cached[0][2]<stat.st_size
                samples=list(cached[1]['samples']) if incremental else []
                with path.open('rb') as handle:
                    if incremental:handle.seek(cached[0][2])
                    for raw in handle:
                        if not raw.endswith(b'\n'):
                            # A crash may leave a partial final line. Never cache its offset.
                            try:
                                sample=json.loads(raw)
                                if isinstance(sample,dict) and 'id' in sample:samples.append(sample)
                            except (ValueError,UnicodeError):pass
                            self.cache.pop(path,None)
                            return dict(schema_version=1,samples=samples)
                        if raw.strip():samples.append(json.loads(raw))
                if any(not isinstance(sample,dict) or 'id' not in sample for sample in samples):raise ValueError('baris tidak valid')
                doc=dict(schema_version=1,samples=samples)
            else:
                doc=json.loads(path.read_text())
                if not isinstance(doc,dict) or doc.get('schema_version')!=1 or not isinstance(doc.get('samples'),list):raise ValueError('format tidak valid')
        except (ValueError,UnicodeError) as exc:
            raise ValueError('File telemetri rusak: '+str(path)) from exc
        self.cache[path]=(signature,doc)
        self.cache.move_to_end(path)
        while len(self.cache)>16:self.cache.popitem(last=False)
        return doc

    def recover_tail(self,path):
        if not path.exists() or not path.stat().st_size:return
        with path.open('r+b') as handle:
            handle.seek(-1,os.SEEK_END)
            if handle.read(1)==b'\n':return
            end=handle.tell();offset=end;tail=b''
            while offset:
                size=min(offset,8192);offset-=size;handle.seek(offset)
                tail=handle.read(size)+tail
                if b'\n' in tail:break
            position=tail.rfind(b'\n')
            fragment=tail[position+1:];start=offset+position+1
            try:
                sample=json.loads(fragment)
                if not isinstance(sample,dict) or 'id' not in sample:raise ValueError()
            except (ValueError,UnicodeError):
                # Preserve the interrupted bytes before repairing the file.
                archive=path.with_name(path.name+'.partial-'+str(time.time_ns()))
                with archive.open('xb') as backup:
                    backup.write(fragment);backup.flush();os.fsync(backup.fileno())
                handle.truncate(start)
            else:
                handle.seek(0,os.SEEK_END);handle.write(b'\n')
            handle.flush();os.fsync(handle.fileno())
        self.cache.pop(path,None)

    def append_records(self,path,samples):
        if not samples:return
        path.parent.mkdir(parents=True,exist_ok=True)
        # Validate serialization before touching the destination.
        raw=b''.join((json.dumps(sample,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n').encode() for sample in samples)
        self.recover_tail(path)
        with path.open('ab') as handle:
            offset=handle.tell()
            try:
                handle.write(raw);handle.flush();os.fsync(handle.fileno())
            except OSError:
                handle.truncate(offset);handle.flush();os.fsync(handle.fileno())
                raise
        cached=self.cache.get(path)
        if cached and cached[0][2]==offset:
            stat=path.stat()
            cached[1]['samples'].extend(samples)
            self.cache[path]=((stat.st_ino,stat.st_mtime_ns,stat.st_size),cached[1])
        else:self.cache.pop(path,None)

    def migrate_json(self):
        count=0
        for source in sorted((self.root/'telemetry').glob('*.json')):
            doc=self.read_day(source);target=source.with_suffix('.jsonl')
            known={r['id'] for r in self.read_day(target)['samples']} if target.exists() else set()
            missing=[r for r in doc['samples'] if r['id'] not in known]
            self.append_records(target,missing);count+=len(missing)
        return count

    @staticmethod
    def row(sample):
        row=sample.copy()
        row.pop('received_at',None)
        row['details']=json.dumps(row.get('details',{}),allow_nan=False)
        return row

    def latest(self,device_id):
        for path in reversed(self.paths(device_id)):
            stat=path.stat();signature=(path,stat.st_ino,stat.st_mtime_ns,stat.st_size)
            cached=self.latest_cache.get(device_id)
            if cached and cached[0]==signature:return self.row(cached[1])
            sample=self.rollups.read(path)['latest']
            if sample:
                self.latest_cache[device_id]=(signature,sample)
                return self.row(sample)
        return None

    def history(self,device_id,since=None,after_id=0,limit=None,raw=False):
        rows=[]
        for path in self.paths(device_id,since):
            rows.extend((s if raw else self.row(s)) for s in self.read_day(path)['samples'] if s['id']>after_id and (since is None or s['received']>=since))
        rows.sort(key=lambda r:r['id'])
        return rows if limit is None else rows[:limit]

    def network_history(self,since,raw=False):
        rows=[row for d in self.devices(kind='network') for row in self.history(d['id'],since=since,raw=raw)]
        return sorted(rows,key=lambda r:(r['received'],r['id']))

    def append(self,device_id,received,rssi,local_ms=None,internet_ok=None,internet_ms=None,details=None,record_id=None):
        self.valid_id(device_id)
        timestamp=datetime.fromtimestamp(received,self.timezone)
        date=timestamp.strftime('%d-%m-%Y')
        path=self.root/'telemetry'/(device_id+'-'+date+'.jsonl')
        previous=self.latest(device_id)
        if record_id is None:
            record_id=max(time.time_ns()//1000,self.last_id+1,(previous['id']+1) if previous else 1)
        elif path.exists() and any(s['id']==record_id for s in self.read_day(path)['samples']):return record_id
        sample=dict(id=record_id,device_id=device_id,received=received,received_at=timestamp.isoformat(),rssi=rssi,local_ms=local_ms,internet_ok=internet_ok,internet_ms=internet_ms,details=json.loads(details) if isinstance(details,str) else copy.deepcopy(details or {}))
        self.append_records(path,[sample])
        stat=path.stat()
        if previous is None or record_id>previous['id']:
            self.latest_cache[device_id]=((path,stat.st_ino,stat.st_mtime_ns,stat.st_size),sample)
        self.last_id=max(self.last_id,record_id)
        try:self.rollups.read(path)
        except (OSError,ValueError):
            # Source append already durable. Rebuild the derived view on next read.
            self.rollups.cache.pop(path,None)
        return record_id
