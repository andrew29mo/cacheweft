"""Materialize a verified pinned artifact, then exec the existing batching service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def call(cli,socket,*args):
    result=subprocess.run([cli,'--socket',socket,*map(str,args)],capture_output=True,text=True,timeout=12)
    if result.returncode:raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)


def prepare(args):
    admitted=False;state=None
    try:
        status=call(args.cacheweft,args.socket,'fetch',args.request_id,args.artifact,'--seconds',args.seconds)
        admitted=True;state=status['request']['state'];until=time.monotonic()+args.seconds+5
        while state=='WAITING':
            if time.monotonic()>=until:raise TimeoutError('Check request status: local startup wait expired')
            time.sleep(.05);status=call(args.cacheweft,args.socket,'status',args.request_id);state=status['request']['state']
        if state!='SUCCEEDED':raise RuntimeError('Artifact request ended '+state)
        if not status['available']:raise RuntimeError('Historical request succeeded but content is unavailable; use a new ID after explicit recovery/GC')
        if status['object']['digest']!=args.digest:raise RuntimeError('Requested release digest differs from trusted manifest result')
        # Pin is a shared boolean, not a per-process lease. Never automatically unpin it.
        call(args.cacheweft,args.socket,'pin',args.digest,'--value','true','--request',args.request_id)
        directory=Path(args.model_dir);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        model=directory/(args.digest+'.onnx')
        call(args.cacheweft,args.socket,'read',args.digest,'--output',model)
        if hashlib.sha256(model.read_bytes()).hexdigest()!=args.digest:raise RuntimeError('Materialized model digest mismatch')
        return model
    except BaseException:
        if admitted and state=='WAITING':
            try:call(args.cacheweft,args.socket,'cancel',args.request_id)
            except Exception:print('Cancellation not confirmed; inspect the request status',file=sys.stderr)
        raise


def main():
    p=argparse.ArgumentParser(description='Cacheweft model startup adapter for the existing tiny-batch-service')
    p.add_argument('--cacheweft',required=True);p.add_argument('--socket',required=True)
    p.add_argument('--artifact',required=True);p.add_argument('--digest',required=True);p.add_argument('--request-id',required=True)
    p.add_argument('--model-dir',required=True);p.add_argument('--service',required=True)
    p.add_argument('--seconds',type=float,default=30);p.add_argument('--port',type=int,default=8080)
    p.add_argument('--batch-size',type=int,default=4);p.add_argument('--capacity',type=int,default=16)
    p.add_argument('--socket-fd',type=int,help=argparse.SUPPRESS)
    a=p.parse_args()
    if not re.fullmatch('[0-9a-f]{64}',a.digest):p.error('Expected a trusted SHA-256 release digest')
    if not .1<=a.seconds<=120 or not 1<=a.port<=65535 or not 1<=a.batch_size<=a.capacity:p.error('Invalid deadline/port/capacity')
    a.cacheweft=str(Path(a.cacheweft).absolute());a.service=str(Path(a.service).absolute())
    try:
        model=prepare(a)
        print(json.dumps(dict(event='verified_model_ready',sha256=a.digest,request_id=a.request_id,pinned=True)),flush=True)
        argv=[a.service,str(model),'--batch-size',str(a.batch_size),'--capacity',str(a.capacity),'--wait-ms','2','--port',str(a.port)]
        if a.socket_fd is not None:argv+=['--socket-fd',str(a.socket_fd)]
        os.execv(a.service,argv)
    except (RuntimeError,OSError,ValueError,KeyError,TimeoutError) as exc:
        print(type(exc).__name__+': '+str(exc),file=sys.stderr);raise SystemExit(1)

if __name__=='__main__':main()
