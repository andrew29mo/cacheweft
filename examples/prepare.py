"""Provision a reference independently of the cache, with pinned upstream metadata."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import urllib.request

LOCK=Path(__file__).with_name('model.json')

def url(model,host,path):return f'https://{host}/{model["repository"]}/{model["revision"]}/{path}'

def fetch(url,limit):
    # Bootstrap only: unlike the product this may resolve DNS. Never follow redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(url,timeout=20) as response:
        data=response.read(limit+1)
        if len(data)>limit:raise ValueError('Upstream response exceeds bound')
        return data

def prepare(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    model=json.loads(LOCK.read_text());pointer_url=url(model,'raw.githubusercontent.com',model['path'])
    pointer=fetch(pointer_url,1024).decode('ascii')
    expected=f'version https://git-lfs.github.com/spec/v1\noid sha256:{model["sha256"]}\nsize {model["size"]}\n'
    if pointer!=expected:raise ValueError('Pinned upstream LFS metadata differs from trust lock')
    card=fetch(url(model,'raw.githubusercontent.com',model['card_path']),32768)
    license=fetch(url(model,'raw.githubusercontent.com',model['license_path']),32768)
    if b'Apache 2.0' not in card or b'Apache License' not in license:raise ValueError('Upstream license evidence changed')
    # media's path is /media/OWNER/REPO/COMMIT/PATH, not a new download trust source.
    payload_url='https://media.githubusercontent.com/media/'+model['repository']+'/'+model['revision']+'/'+model['path']
    body=fetch(payload_url,model['size'])
    if len(body)!=model['size'] or hashlib.sha256(body).hexdigest()!=model['sha256']:raise ValueError('Reference bytes mismatch')
    (output/'reference.onnx').write_bytes(body)
    record=dict(model=model,pointer_url=pointer_url,pointer=pointer,payload_url=payload_url,
                license_url=url(model,'raw.githubusercontent.com',model['license_path']),license_sha256=hashlib.sha256(license).hexdigest(),
                card_url=url(model,'raw.githubusercontent.com',model['card_path']),card_sha256=hashlib.sha256(card).hexdigest(),
                reference_sha256=hashlib.sha256(body).hexdigest(),reference_size=len(body),redirects_allowed=False,proxies_used=False)
    (output/'provenance.json').write_text(json.dumps(record,indent=2)+'\n')
    return record

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();print(json.dumps(prepare(a.output)))
