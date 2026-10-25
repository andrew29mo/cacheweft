"""Real loopback TLS source for three independently hashed bounded artifacts."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time

BODIES={'a':bytes(range(256))*4096,'b':bytes(reversed(range(256)))*4096,'c':b'closeout'*262144}

class Origin:
    def __init__(self,root):
        root=Path(root);root.mkdir();self.cert=root/'cert.pem';key=root/'key.pem';self.lock=threading.Lock()
        self.active=0;self.calls=[];self.modes={k:('normal',threading.Event(),threading.Event()) for k in BODIES}
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(self.cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):
                name=self.path.removeprefix('/').removesuffix('.bin')
                if name not in BODIES:self.send_error(404);return
                with owner.lock:
                    mode,arrived,release=owner.modes[name];owner.active+=1
                    entry=dict(name=name,mode=mode,range=self.headers.get('Range'));owner.calls.append(entry)
                body=BODIES[name];etag='"'+hashlib.sha256(body).hexdigest()+'"';start=0
                try:
                    if mode=='hold':arrived.set();release.wait(15)
                    if entry['range']:
                        match=re.fullmatch(r'bytes=(\d+)-',entry['range'])
                        if not match or int(match[1])>=len(body):self.send_error(416);return
                        if self.headers.get('If-Range')==etag:start=int(match[1])
                    payload=body[start:];self.send_response(206 if start else 200)
                    self.send_header('Content-Length',str(len(payload)));self.send_header('ETag',etag);self.send_header('Connection','close')
                    if start:self.send_header('Content-Range',f'bytes {start}-{len(body)-1}/{len(body)}')
                    self.end_headers()
                    if mode in ('prefix','gated') and not start:
                        self.wfile.write(payload[:262144]);self.wfile.flush();arrived.set();release.wait(15)
                        if mode=='prefix':return
                        payload=payload[262144:]
                    self.wfile.write(payload);self.wfile.flush();arrived.set()
                except (OSError,ssl.SSLError):pass
                finally:
                    with owner.lock:owner.active-=1
                    self.close_connection=True
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.daemon_threads=True
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(self.cert,key);self.server.socket=ctx.wrap_socket(self.server.socket,server_side=True)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin=f'https://localhost:{self.server.server_port}'
    def mode(self,name,mode):
        with self.lock:self.modes[name]=(mode,threading.Event(),threading.Event());return self.modes[name][1:]
    def release(self,name):
        with self.lock:self.modes[name][2].set()
    def idle(self):
        until=time.monotonic()+3
        while time.monotonic()<until:
            with self.lock:
                if self.active==0:return
            time.sleep(.01)
        raise TimeoutError('Origin handlers did not exit')
    def close(self):
        for name in BODIES:self.release(name)
        self.server.shutdown();self.server.server_close();self.thread.join(3);self.idle()
