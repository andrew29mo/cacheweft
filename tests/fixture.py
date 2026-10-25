"""Controlled real TLS origin; never imported by cacheweft runtime."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
import ssl
import subprocess
import threading
import time

DATA=bytes(range(256))*4096


class Origin:
    def __init__(self,root):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.calls=[]; self.sent=0; self.mode='normal'; self.release=threading.Event(); self.arrived=threading.Event()
        cert,key=self.root/'cert.pem',self.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                rang=self.headers.get('Range'); tag=self.headers.get('If-Range')
                owner.calls.append(dict(range=rang,if_range=tag,encoding=self.headers.get('Accept-Encoding')))
                mode=owner.mode
                if mode=='hold':
                    owner.arrived.set();owner.release.wait(5)
                start=int(rang.split('=')[1].split('-')[0]) if rang else 0
                status=206 if rang else 200
                etag='"v1"'
                if mode=='ignore': status=200;start=0
                if mode=='changed' and rang: status=200;start=0;etag='"v2"'
                if mode=='weak': etag='W/"v1"'
                if mode=='none': etag=None
                if mode=='416':status=416
                if rang and start>=len(DATA):status=416
                body=DATA[start:]
                if status==416:body=b''
                if mode=='corrupt':body=b'x'*len(body)
                self.send_response(status)
                if etag:self.send_header('ETag',etag)
                self.send_header('Content-Length',str(len(body)+(1 if mode=='wrong_length' else 0)))
                if mode=='encoded': self.send_header('Content-Encoding','gzip')
                if status==206:self.send_header('Content-Range',f'bytes {start+1 if mode=="bad_range" else start}-{len(DATA)-1}/{len(DATA)}')
                if status==416:self.send_header('Content-Range',f'bytes */{len(DATA)}')
                self.end_headers()
                try:
                    if mode=='drop' and not rang:
                        part=body[:131072];self.wfile.write(part);self.wfile.flush();owner.sent+=len(part);self.close_connection=True;return
                    self.wfile.write(body);self.wfile.flush();owner.sent+=len(body)
                except (BrokenPipeError,ConnectionResetError,ssl.SSLError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        self.server.socket=context.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'https://localhost:{self.server.server_port}/artifact'
        self.origin=f'https://localhost:{self.server.server_port}'
        self.cert=str(cert)

    def close(self):
        self.release.set();self.server.shutdown();self.server.server_close();self.thread.join(3)
