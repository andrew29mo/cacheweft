import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import sys
import tempfile
import time

from .core import CacheError
from .service import serve,send,receive,exact,token


def call(path,request,output=None):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3);sock.connect(str(path));send(sock,request)
        response=receive(sock,time.monotonic()+3)
        if not response.get('ok'):raise CacheError(response.get('error','Invalid response'))
        if request['op']=='read':
            if output is None:raise CacheError('Output file required')
            out=Path(output);fd,tmp=tempfile.mkstemp(prefix='.'+out.name+'-',dir=out.parent)
            total=0
            try:
                with os.fdopen(fd,'wb') as f:
                    deadline=time.monotonic()+12
                    while True:
                        n=struct.unpack('!I',exact(sock,4,deadline))[0]
                        if not n:break
                        if n>65536 or total+n>response['result']['size']:raise CacheError('Invalid stream length')
                        f.write(exact(sock,n,deadline));total+=n
                    if total!=response['result']['size']:raise CacheError('Truncated stream')
                    f.flush();os.fsync(f.fileno())
                os.replace(tmp,out)
            finally:
                Path(tmp).unlink(missing_ok=True)
        return response['result']


def main():
    p=argparse.ArgumentParser();p.add_argument('--socket');sub=p.add_subparsers(dest='op',required=True)
    s=sub.add_parser('serve');s.add_argument('--root',required=True);s.add_argument('--config',required=True)
    sub.add_parser('info');s=sub.add_parser('status');s.add_argument('id')
    for op in ('fetch','resume'):
        s=sub.add_parser(op);s.add_argument('id');s.add_argument('artifact');s.add_argument('--seconds',type=float,default=30)
        if op=='resume':s.add_argument('--source',required=True)
    s=sub.add_parser('cancel');s.add_argument('id')
    s=sub.add_parser('pin');s.add_argument('digest');s.add_argument('--value',choices=['true','false'],required=True);s.add_argument('--request',required=True)
    sub.add_parser('gc-plan');s=sub.add_parser('gc-run');s.add_argument('--plan',required=True)
    s=sub.add_parser('read');s.add_argument('digest');s.add_argument('--output',required=True)
    a=p.parse_args()
    try:
        if a.op=='serve':serve(a.root,a.config);return
        if not a.socket:p.error('--socket is required')
        def api(q,output=None):return call(a.socket,q,output)
        q=dict(op=a.op)
        if a.op in ('status','fetch','resume','cancel'):q['id']=a.id
        if a.op in ('fetch','resume','cancel','pin','gc-run'):q['epoch']=api(dict(op='info'))['epoch']
        if a.op in ('fetch','resume'):q.update(artifact=a.artifact,seconds=a.seconds)
        if a.op=='resume':
            q['source']=a.source;q['token']=token(api(dict(op='status',id=a.source)))
        if a.op=='cancel':q['token']=token(api(dict(op='status',id=a.id)))
        if a.op=='pin':
            status=api(dict(op='status',id=a.request));obj=status['object']
            if obj is None or obj['digest']!=a.digest:raise CacheError('Request does not identify object')
            q.update(digest=a.digest,value=a.value=='true',token=dict(epoch=status['epoch'],generation=obj['generation'],revision=obj['revision']))
        if a.op=='gc-run':
            plan=Path(a.plan)
            if plan.stat().st_size>16384:raise CacheError('Plan file too large')
            q['plan']=json.loads(plan.read_text())
        if a.op=='read':q['digest']=a.digest
        print(json.dumps(api(q,a.output if a.op=='read' else None),allow_nan=False))
    except (CacheError,OSError,ValueError,KeyError,sqlite3.Error) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(1)

if __name__=='__main__':main()
