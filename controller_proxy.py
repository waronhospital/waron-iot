"""Restricted reverse proxy for the ESP portal; no arbitrary target URLs."""
import gzip
import http.client
import ipaddress
import json
import re
from urllib.parse import unquote, urlsplit

ROUTES={'/','/setup','/health','/api/status','/api/scan','/api/wifi','/api/config','/api/ota'}
WRITES={'/api/wifi','/api/config','/api/ota'}
MAX_UPLOAD=2*1024*1024

def rewrite_html(body,prefix):
    text=body.decode('utf-8')
    return re.sub(r'''(["'])/(?!/)''',lambda m:m[1]+prefix+'/',text).encode('utf-8')

def serve(handler,connect):
    parsed=urlsplit(handler.path)
    match=re.fullmatch(r'/controllers/([A-Za-z0-9_-]{1,40})(/.*)?',parsed.path)
    if not match:return handler.reply(404,{'error':'Controller tidak ditemukan'})
    device_id=match[1];prefix='/controllers/'+device_id;path=unquote(match[2] or '/')
    if path not in ROUTES:return handler.reply(404,{'error':'Halaman controller tidak ditemukan'})
    if handler.command=='POST' and path not in WRITES:return handler.reply(405,{'error':'Metode tidak diizinkan'})
    if handler.command=='GET' and path in WRITES:return handler.reply(405,{'error':'Metode tidak diizinkan'})
    if not match[2]:
        handler.send_response(302);handler.send_header('Location',prefix+'/');handler.send_header('Content-Length','0');handler.end_headers();return
    with connect() as store:
        device=store.device(device_id);latest=store.latest(device_id) if device else None
    if not device or not device.get('enabled',True):return handler.reply(404,{'error':'Perangkat tidak aktif atau tidak ditemukan'})
    details=json.loads(latest['details']) if latest else {}
    try:
        ip=ipaddress.IPv4Address(details.get('wifi',{}).get('ip',''))
        if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:raise ValueError()
    except ValueError:return handler.reply(409,{'error':'IP LAN controller belum tersedia'})
    # Targets always come from device telemetry, never a query parameter.
    body=None
    if handler.command=='POST':
        origin=handler.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=handler.headers.get('Host',''):return handler.reply(403,{'error':'Origin tidak diizinkan'})
        try:
            size=int(handler.headers.get('Content-Length','0'))
            if not 0<size<=MAX_UPLOAD:raise ValueError()
        except ValueError:return handler.reply(413,{'error':'Ukuran permintaan harus 1 byte–2 MiB'})
        body=handler.rfile.read(size)
        if len(body)!=size:return handler.reply(400,{'error':'Permintaan tidak lengkap'})
    headers={key:handler.headers[key] for key in ('Authorization','Content-Type','X-Device-Token') if handler.headers.get(key)}
    headers['Accept-Encoding']='identity'
    upstream=http.client.HTTPConnection(str(ip),80,timeout=120 if path=='/api/ota' else 15)
    try:
        upstream.request(handler.command,path,body=body,headers=headers)
        response=upstream.getresponse();data=response.read(MAX_UPLOAD+1)
        if len(data)>MAX_UPLOAD:return handler.reply(502,{'error':'Respons controller terlalu besar'})
        content_type=response.getheader('Content-Type','application/octet-stream')
        if 'text/html' in content_type:
            if response.getheader('Content-Encoding')=='gzip':data=gzip.decompress(data)
            data=rewrite_html(data,prefix)
        handler.send_response(response.status)
        handler.send_header('Content-Type',content_type)
        handler.send_header('Content-Length',str(len(data)))
        handler.send_header('Cache-Control','no-store')
        handler.send_header('X-Content-Type-Options','nosniff')
        if response.status==401:
            handler.send_header('WWW-Authenticate','Basic realm="ESP '+device_id+'"')
        if 'text/html' not in content_type and response.getheader('Content-Encoding'):
            handler.send_header('Content-Encoding',response.getheader('Content-Encoding'))
        handler.end_headers();handler.wfile.write(data)
    except (OSError,http.client.HTTPException,ValueError,EOFError):
        handler.reply(502,{'error':'Controller tidak terjangkau atau respons tidak valid; pastikan server dapat mengakses IP ESP'})
    finally:upstream.close()
