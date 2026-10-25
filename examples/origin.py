"""Controlled real TLS origin serving independently provisioned model bytes."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import time

class Origin:
    def __init__(self,root,body):
        root=Path(root);root.mkdir();self.body=body;self.mode='normal';self.calls=[];self.sent=0
        self.arrived=threading.Event();self.release=threading.Event();self.etag='"'+hashlib.sha256(body).hexdigest()+'"'
        self.cert=root/'cert.pem';key=root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(self.cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path!='/model.onnx':self.send_error(404);return
                start=0;rang=self.headers.get('Range');tag=self.headers.get('If-Range')
                if rang:
                    m=re.fullmatch(r'bytes=(\d+)-',rang)
                    if not m or int(m[1])>=len(body):self.send_error(416);return
                    if tag==owner.etag:start=int(m[1])
                payload=owner.body[start:];mode=owner.mode
                if mode=='corrupt':payload=bytes([payload[0]^1])+payload[1:]
                entry=dict(range=rang,if_range=tag,status=206 if start else 200,start=start,successful_body_write_bytes=0)
                owner.calls.append(entry)
                self.send_response(entry['status']);self.send_header('Content-Length',str(len(payload)));self.send_header('ETag',owner.etag)
                self.send_header('Connection','close')
                if start:self.send_header('Content-Range',f'bytes {start}-{len(body)-1}/{len(body)}')
                self.end_headers()
                try:
                    if mode=='partial_hold' and not rang:
                        # A controlled checkpoint boundary; leave the stream stalled until cancellation/crash.
                        prefix=262144;self.wfile.write(payload[:prefix]);self.wfile.flush()
                        entry['successful_body_write_bytes']+=prefix;owner.sent+=prefix;owner.arrived.set()
                        owner.release.wait(15)
                        return  # Close without sending the advertised remainder.
                    self.wfile.write(payload);self.wfile.flush();entry['successful_body_write_bytes']+=len(payload);owner.sent+=len(payload)
                except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
                finally:self.close_connection=True
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(self.cert,key)
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'https://localhost:{self.server.server_port}/model.onnx';self.origin=f'https://localhost:{self.server.server_port}';self.cert=str(self.cert)
    def close(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(3)
