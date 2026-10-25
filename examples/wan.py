"""One Internet sample through the formal CLI; independent reference and inference."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
from .demo import MODEL,ModelDaemon,Samples,wait,read,stop,disk,usage
from tests.daemon_fixture import require
import time


def run(reference,cli,inference):
    url='https://media.githubusercontent.com/media/'+MODEL['repository']+'/'+MODEL['revision']+'/'+MODEL['path']
    addresses=sorted({x[4][0] for x in socket.getaddrinfo('media.githubusercontent.com',443,socket.AF_INET,socket.SOCK_STREAM)})
    require(addresses,'No provisioned IPv4 address')
    with tempfile.TemporaryDirectory(prefix='cw-wan-',dir='/tmp') as tmp:
        base=Path(tmp);base.chmod(0o755);origin=SimpleNamespace(origin='https://media.githubusercontent.com',url=url,cert=None)
        d=ModelDaemon(base/'daemon',origin,cli);d.value['address']=addresses[0];d.value['attempts']=6;d.write();sampler=None
        try:
            d.start();sampler=Samples(d).start();start=time.monotonic()
            d.cli('fetch','wan',MODEL['id'],'--seconds','120');final=wait(d,'wan',125);elapsed=time.monotonic()-start
            require(final['request']['state']=='SUCCEEDED' and final['available'],'Real Internet fetch failed: '+str(final['request']))
            path,r=read(d,'wan');memory=sampler.stop();observed_usage=usage(d);usage_disk=disk(d.root);cleanup=stop(d)
            result=base/'inference.json'
            subprocess.run([str(Path(inference).absolute()),str(Path(__file__).with_name('infer.py')),'--reference',str(Path(reference).resolve()),'--paths',json.dumps(dict(wan=str(path))),'--output',str(result)],check=True,capture_output=True,text=True,timeout=40)
            return dict(url=url,provisioned_addresses=addresses,pinned_address=addresses[0],fetch_seconds=elapsed,final=final,read=r,memory=memory,disk=usage_disk,usage=observed_usage,cleanup=cleanup,
                inference=json.loads(result.read_text()),scope='One real Internet fetch; DNS provisioning outside cache; no WAN speedup or throughput distribution claim')
        finally:
            try:
                if sampler and sampler.thread.is_alive():sampler.stop()
            finally:d.stop()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',required=True);p.add_argument('--cli',required=True);p.add_argument('--inference-python',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    Path(a.output).write_text(json.dumps(run(a.reference,a.cli,a.inference_python),indent=2)+'\n')
