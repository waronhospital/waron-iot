import gzip
import io
import json
import unittest
from unittest.mock import patch
import controller_proxy as proxy

class ControllerTests(unittest.TestCase):
    def handler(self,path='/controllers/WMS-001/',method='GET'):
        class Handler:
            command=method
            headers={}
            rfile=io.BytesIO()
            wfile=io.BytesIO()
            def reply(self,status,body):self.status=status;self.error=body
            def send_response(self,status):self.status=status
            def send_header(self,key,value):self.sent[key]=value
            def end_headers(self):pass
        h=Handler();h.path=path;h.sent={};return h
    def store(self,ip='192.168.0.75'):
        class Store:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def device(self,id):return {'enabled':True}
            def latest(self,id):return {'details':json.dumps({'wifi':{'ip':ip}})}
        return Store
    def test_rewrite(self):
        content=b'''<a href="/setup">Setup</a><script>fetch('/api/status');fetch('/api/config');x.open('POST','/api/ota');</script>'''
        result=proxy.rewrite_html(content,'/controllers/WMS-001').decode()
        for path in ['setup','api/status','api/config','api/ota']:self.assertIn('/controllers/WMS-001/'+path,result)
    def test_blocks_arbitrary_routes(self):
        h=self.handler('/controllers/WMS-001/http://other/');proxy.serve(h,self.store());self.assertEqual(h.status,404)
    def test_blocks_loopback(self):
        h=self.handler();proxy.serve(h,self.store('127.0.0.1'));self.assertEqual(h.status,409)
    def test_gzip_portal(self):
        with patch.object(proxy.http.client,'HTTPConnection') as connection:
            response=connection.return_value.getresponse.return_value
            response.status=200
            response.getheader.side_effect=lambda name,default=None:{'Content-Type':'text/html','Content-Encoding':'gzip'}.get(name,default)
            response.read.return_value=gzip.compress(b'<a href="/setup">Setup</a>')
            h=self.handler();proxy.serve(h,self.store())
            self.assertEqual(h.status,200);self.assertIn(b'/controllers/WMS-001/setup',h.wfile.getvalue())
            connection.assert_called_once_with('192.168.0.75',80,timeout=15)
    def test_reject_cross_origin_write(self):
        h=self.handler('/controllers/WMS-001/api/config','POST');h.headers={'Host':'dashboard.local','Origin':'http://other.local'}
        proxy.serve(h,self.store());self.assertEqual(h.status,403)
