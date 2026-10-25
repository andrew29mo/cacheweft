"""Leave a real unfinished publish; verify unchanged journal after normal boot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import sqlite3

from cacheweft import Cache
from tests.fixture import DATA,Origin

BASE=Path('/opt/cacheweft/reboot')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def states(root):
    db=sqlite3.connect('file:'+str(root/'catalog.db')+'?mode=ro',uri=True)
    try:
        return dict(objects=db.execute('SELECT digest,generation,state,offset FROM objects').fetchall(),
                    requests=db.execute('SELECT id,generation,state FROM requests').fetchall())
    finally:db.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','finish']);a=p.parse_args()
    root=BASE/'cache'
    if a.action=='prepare':
        BASE.mkdir(parents=True,exist_ok=False);origin=Origin(BASE/'tls')
        try:
            code='''import os,sys
from cacheweft import Cache,Spec
from cacheweft.http import HTTPS
def hook(p,i):
 if p=='publish_intent':os._exit(73)
c=Cache(sys.argv[1],[Spec('fixture',sys.argv[2],1048576,sys.argv[5])],HTTPS(sys.argv[3],'127.0.0.1',sys.argv[4]),hook=hook)
c.submit('reboot_request','fixture');c.wait('reboot_request');os._exit(74)
'''
            r=subprocess.run([sys.executable,'-c',code,str(root),origin.url,origin.origin,origin.cert,hashlib.sha256(DATA).hexdigest()],timeout=20,capture_output=True,text=True)
            if r.returncode!=73:raise RuntimeError('Child did not stop at publish intent: '+r.stderr)
        finally:origin.close()
        v=dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               catalog_sha256=digest(root/'catalog.db'),stage_sha256=[digest(x) for x in (root/'staging').iterdir()],
               target_absent=not any((root/'objects').iterdir()),crash_exit=73,states=states(root))
        if v['states']['objects'][0][2]!='PUBLISHING' or v['states']['requests'][0][2]!='WAITING':raise RuntimeError('Not an unfinished publication')
        (BASE/'prepared.json').write_text(json.dumps(v,indent=2)+'\n')
        print(json.dumps(v))
    else:
        before=json.loads((BASE/'prepared.json').read_text());boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        same=digest(root/'catalog.db')==before['catalog_sha256']
        pre_catalog_hash=digest(root/'catalog.db')
        stage_hashes=[digest(x) for x in (root/'staging').iterdir()]
        pre_states=states(root)
        if boot==before['boot_id'] or not same or stage_hashes!=before['stage_sha256']:raise RuntimeError('Boot did not change or retained state changed before recovery')
        with Cache(root,[],None) as c:
            status=c.status('reboot_request')
            if status['request']['state']!='SUCCEEDED' or not status['available']:raise RuntimeError('Pending publish not recovered')
            with c.read(status['request']['digest']) as f:
                if f.read()!=DATA:raise RuntimeError('Recovered content differs')
        v=dict(boot_changed=True,boot_id_after=boot,catalog_unchanged_before_recovery=True,states_before_recovery=pre_states,
               catalog_sha256_before_recovery=pre_catalog_hash,stage_sha256_before_recovery=stage_hashes,recovered_request=status['request'],
               before=before,recovered_correct=True,normal_reboot=True,power_loss_tested=False)
        (BASE/'finished.json').write_text(json.dumps(v,indent=2)+'\n');print(json.dumps(v))

if __name__=='__main__':main()
