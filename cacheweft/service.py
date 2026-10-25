"""Private Unix socket API. One bounded control request per connection."""
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import re
import sqlite3
import socket
import socketserver
import stat
import struct
import threading
import time

from .core import Cache,Spec,CacheError
from .http import HTTPS

FRAME=16384


def encode(value):return json.dumps(value,separators=(',',':'),allow_nan=False).encode()


def exact(sock,n,deadline):
    result=bytearray()
    while len(result)<n:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Control frame deadline')
        sock.settimeout(remaining);part=sock.recv(n-len(result))
        if not part:raise CacheError('Truncated frame')
        result.extend(part)
    return bytes(result)


def send(sock,value):
    data=encode(value)
    if len(data)>FRAME:raise CacheError('Response too large')
    sock.sendall(struct.pack('!I',len(data))+data)


def receive(sock,deadline):
    length=struct.unpack('!I',exact(sock,4,deadline))[0]
    if not 1<=length<=FRAME:raise CacheError('Invalid frame length')
    value=json.loads(exact(sock,length,deadline),parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    if not isinstance(value,dict):raise CacheError('Object frame required')
    return value


def config(path):
    p=Path(path);info=p.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise CacheError('Config must be an owned private regular file')
    if info.st_size>65536:raise CacheError('Config too large')
    value=json.loads(p.read_text(),parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite config')))
    required={'revision','origin','address','entries'};optional={'cafile','budget','records','jobs','readers','attempts','db_pages'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-optional:raise CacheError('Invalid config fields')
    if not isinstance(value['entries'],list) or not 1<=len(value['entries'])<=128:raise CacheError('Manifest entry count')
    specs=[]
    transport=HTTPS(value['origin'],value['address'],value.get('cafile'))
    for item in value['entries']:
        if not isinstance(item,dict) or set(item)!={'id','url','size','sha256'}:raise CacheError('Invalid artifact entry')
        if not isinstance(item['id'],str) or not re.fullmatch('[A-Za-z0-9_.-]{1,64}',item['id']):raise CacheError('Artifact ID')
        if not isinstance(item['url'],str) or len(item['url'])>2048 or any(ord(ch)<33 for ch in item['url']):raise CacheError('Artifact URL')
        # Validate source identity without connecting to it.
        from urllib.parse import urlsplit
        u=urlsplit(item['url'])
        if u.scheme!='https' or u.hostname!=transport.host or (u.port or 443)!=transport.port or not u.path.startswith('/') or u.fragment or u.username or u.password:raise CacheError('Artifact outside origin')
        specs.append(Spec(item['id'],item['url'],item['size'],item['sha256']))
    limits={}
    for name,default,low,high in [('budget',16777216,65536,1073741824),('records',256,1,4096),('jobs',16,1,16),('readers',8,1,8),('attempts',6,1,16),('db_pages',1024,16,4096)]:
        x=value.get(name,default)
        if type(x) is not int or not low<=x<=high:raise CacheError('Invalid '+name)
        limits[name]=x
    if type(value['revision']) is not int or not 1<=value['revision']<=1000000:raise CacheError('Invalid manifest revision')
    return value,specs,transport,limits


def token(status):
    r=status['request']
    return dict(epoch=status['epoch'],generation=r['generation'],revision=r['revision'])


class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=False;block_on_close=True
    request_queue_size=16

    def __init__(self,path,cache):
        self.cache=cache;self.slots=threading.BoundedSemaphore(16)
        super().__init__(str(path),Handler);self.timeout=.2

    def process_request(self,request,address):
        if not self.slots.acquire(False):
            try:request.settimeout(.2);send(request,dict(ok=False,error='OVERLOADED'))
            except OSError:pass
            self.shutdown_request(request);return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise

    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        sock=self.request;c=self.server.cache
        try:
            q=receive(sock,time.monotonic()+2);op=q.get('op')
            fields={'info':set(),'status':{'id'},'fetch':{'id','artifact','seconds','epoch'},
                    'resume':{'id','source','artifact','seconds','epoch','token'},'cancel':{'id','token','epoch'},
                    'pin':{'digest','value','token','epoch'},'gc-plan':set(),'gc-run':{'plan','epoch'},'read':{'digest'}}
            if op not in fields or set(q)!={'op'}|fields[op]:raise CacheError('Unknown operation or fields')
            if op in ('fetch','resume','cancel','pin','gc-run') and q['epoch']!=c.epoch:raise CacheError('Stale epoch')
            if op in ('resume','cancel','pin') and not isinstance(q['token'],dict):raise CacheError('Token object required')
            if op in ('fetch','resume'):
                if type(q['seconds']) not in (int,float) or not math.isfinite(q['seconds']) or not .1<=q['seconds']<=120:raise CacheError('Duration must be 0.1..120 seconds')
            if op=='info':result=dict(epoch=c.epoch,manifest_revision=c.manifest_revision,degraded=c.degraded,entries=list(c.specs),usage=c.usage(),sqlite_busy=dict(c.sqlite_busy))
            elif op=='status':result=c.status(q['id'])
            elif op=='fetch':result=c.submit(q['id'],q['artifact'],seconds=q['seconds'],epoch=q['epoch'])
            elif op=='resume':result=c.submit(q['id'],q['artifact'],seconds=q['seconds'],epoch=q['epoch'],resume_from=q['source'],token=q['token'])
            elif op=='cancel':result=c.cancel(q['id'],q['token'])
            elif op=='pin':
                if type(q['value']) is not bool:raise CacheError('Boolean pin required')
                c.pin(q['digest'],q['value'],q['token']);result='OK'
            elif op=='gc-plan':result=c.gc_plan()[:32]
            elif op=='gc-run':
                if not isinstance(q['plan'],list) or len(q['plan'])>32:raise CacheError('Plan limit')
                if any(not isinstance(item,dict) or set(item)!={'digest','generation','revision','epoch'} or not isinstance(item['digest'],str) or not re.fullmatch('[0-9a-f]{64}',item['digest']) or type(item['revision']) is not int or item['revision']<1 for item in q['plan']):raise CacheError('Malformed plan')
                result=c.gc(q['plan'])
            else:
                with c.read(q['digest']) as f:
                    send(sock,dict(ok=True,result=dict(size=os.fstat(f.fileno()).st_size)))
                    deadline=time.monotonic()+10
                    while block:=f.read(65536):
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise TimeoutError('Reader deadline')
                        sock.settimeout(min(2,remaining));sock.sendall(struct.pack('!I',len(block))+block)
                    sock.sendall(b'\0\0\0\0')
                return
            sock.settimeout(2);send(sock,dict(ok=True,result=result))
        except (CacheError,OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
            try:sock.settimeout(.2);send(sock,dict(ok=False,error=type(exc).__name__+': '+str(exc)))
            except (OSError,CacheError):pass


def notify(message):
    """Optional systemd notification; only the main daemon sends readiness."""
    address=os.environ.get('NOTIFY_SOCKET')
    if not address:return
    if address.startswith('@'):address='\0'+address[1:]
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:
        sock.connect(address);sock.sendall(message.encode('ascii'))


def serve(root,configuration):
    if os.geteuid()==0:raise CacheError('Run daemon as an unprivileged account')
    value,specs,transport,limits=config(configuration)
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
    os.umask(0o077)
    with Cache(root,specs,transport,manifest_revision=value['revision'],**limits) as cache:
        path=cache.root/'control.sock';ready=cache.root/'ready.json'
        if len(os.fsencode(str(path)))>=100:raise CacheError('Unix socket path too long')
        if path.exists() or path.is_symlink():
            if not stat.S_ISSOCK(path.lstat().st_mode):raise CacheError('Unexpected socket path')
            path.unlink()
        with Server(path,cache) as server:
            os.chmod(path,0o600)
            temp=cache.root/('ready-'+cache.epoch+'.tmp')
            with open(temp,'x') as f:json.dump(dict(epoch=cache.epoch,pid=os.getpid(),manifest_revision=cache.manifest_revision),f)
            os.replace(temp,ready)
            try:
                notify('READY=1\nSTATUS=Recovered; local control API available')
                while not stop.is_set():server.handle_request()
            finally:
                cache.begin_shutdown()
                try:notify('STOPPING=1')
                except OSError:pass  # Notification loss must not skip cleanup.
                ready.unlink(missing_ok=True);path.unlink(missing_ok=True)
