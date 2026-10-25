"""Requalify immutable measured records; never fabricate new VM measurements."""
import argparse
import ast
import hashlib
import html
import io
import json
from pathlib import Path
import subprocess
import time
import tomllib
import unittest

from closeout.report import verify as close_verify,controls as close_controls,render as close_render
from examples.report import verify as model_verify,controls as model_controls,render as model_render

ROOT=Path(__file__).resolve().parents[1]
MEASURED_COMMIT='c460d7de3dcd87c8cb9dc4cddfa398d47a35ee89'
VALIDATORS={'closeout/report.py','examples/report.py'}
STYLE_ONLY={'closeout/scenario.py'}


def require(value,message):
    if not value:raise ValueError(message)

def sha(data):return hashlib.sha256(data).hexdigest()

def original(path):
    return subprocess.run(['git','show',MEASURED_COMMIT+':'+path],cwd=ROOT,capture_output=True,check=True).stdout

def license_metadata_only(old,current):
    baseline=tomllib.loads(old.decode());updated=tomllib.loads(current.decode())
    project=updated.get('project',{})
    if project.pop('license',None)!='MIT' or project.pop('license-files',None)!=['LICENSE']:return False
    return updated==baseline


def source_bridge(value):
    changes=[]
    for path,expected in value['source'].items():
        old=original(path);current=(ROOT/path).read_bytes()
        require(sha(old)==expected,'Recorded source does not match measured commit: '+path)
        if current==old:continue
        if path=='pyproject.toml':
            require(license_metadata_only(old,current),'Packaging change exceeds MIT metadata')
            kind='MIT license expression and LICENSE inclusion only; runtime/build requirements unchanged'
        elif path in VALIDATORS:kind='stricter validation only; not a measurement producer'
        elif path in STYLE_ONLY:
            require(ast.dump(ast.parse(old),include_attributes=False)==ast.dump(ast.parse(current),include_attributes=False),'Measurement producer AST changed: '+path)
            require(b'\n'.join(line.rstrip() for line in old.splitlines())==b'\n'.join(line.rstrip() for line in current.splitlines()),'Producer change exceeds trailing whitespace: '+path)
            kind='trailing whitespace only; Python AST identical'
        else:raise ValueError('Unqualified measurement/runtime source change: '+path)
        changes.append(dict(path=path,measured_sha256=sha(old),current_sha256=sha(current),classification=kind))
    for name,digest in value['installed_modules'].items():require(sha((ROOT/name).read_bytes())==digest,'Current runtime differs from measured runtime')
    return changes

def qualify():
    records={}
    for name,verify,controls,render in [('closeout',close_verify,close_controls,close_render),('model-demo',model_verify,model_controls,model_render)]:
        path='evidence/'+name+'.json';raw=(ROOT/path).read_bytes();document=(ROOT/('evidence/'+name+'.html')).read_bytes()
        require(raw==original(path) and document==original('evidence/'+name+'.html'),'Measured evidence was rewritten')
        value=json.loads(raw);verify(value)
        require(document==render(value).encode(),'JSON/HTML rendering mismatch')
        records[name]=dict(json_path=path,json_sha256=sha(raw),html_sha256=sha(document),measured_commit=MEASURED_COMMIT,
            measured_source_changes=source_bridge(value),current_runtime_matches=True,verified=True,corruption_controls=controls(value))
    close=json.loads((ROOT/'evidence/closeout.json').read_text());model=json.loads((ROOT/'evidence/model-demo.json').read_text())
    require(close['installed_modules']==model['installed_modules'] and close['wheel_sha256']==model['wheel_sha256'],'Measured records use different runtime/wheel')
    suite=unittest.defaultTestLoader.discover(str(ROOT/'review'),pattern='test_*.py',top_level_dir=str(ROOT))
    def ids(items):
        for item in items:
            if isinstance(item,unittest.TestSuite):yield from ids(item)
            else:yield item.id()
    names=list(ids(suite));stream=io.StringIO();start=time.monotonic();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    require(result.wasSuccessful() and not result.skipped,'Evidence regression suite failed:\n'+stream.getvalue())
    paths=['LICENSE','pyproject.toml',*sorted(VALIDATORS|STYLE_ONLY),*(str(p.relative_to(ROOT)) for p in sorted((ROOT/'review').glob('*.py')))]
    times=[x['seconds'] for x in close['closeout']['samples']]
    return dict(contract='cacheweft-final-review-v1',passed=True,measured_commit=MEASURED_COMMIT,measured_wheel_sha256=close['wheel_sha256'],current_project_license='MIT',records=records,
        qualification_sources={p:sha((ROOT/p).read_bytes()) for p in paths},
        tests=dict(passed=True,tests=result.testsRun,test_ids=names,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),seconds=time.monotonic()-start),
        observed_coverage=dict(samples=len(times),first_seconds=times[0],last_seconds=times[-1],max_gap_seconds=max(b-a for a,b in zip(times,times[1:])),accepted_gap_limit_seconds=1.0),
        limits=dict(new_measurement=False,vm_rerun=False,new_inference_run=False,epoch_end_not_separately_recorded=True,
                    original_memory_gates_unchanged=True,github_settings_modified=False,qualification_writes_project_license=False))

def render(value):
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>Cacheweft final evidence review</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>Final recorded-evidence review</h1><p>This is stricter validation of immutable c460d7d records, not a new VM, inference or performance run. Runtime bytes match the measured version; producer cleanup is trailing whitespace only with identical Python AST. A single saved epoch is validated for presence/format; the original producer checked its live end value, which was not separately stored.</p><pre>'+html.escape(json.dumps(value,indent=2,allow_nan=False))+'</pre></html>\n'

def write_qualification(output):
    out=Path(output)
    require(out.suffix=='.json','Qualification output must use .json')
    protected={ROOT/'evidence'/name for name in ('closeout.json','closeout.html','model-demo.json','model-demo.html')}
    require(out.resolve() not in protected and out.with_suffix('.html').resolve() not in protected,'Output would overwrite measured evidence')
    def save(value):
        out.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');out.with_suffix('.html').write_text(render(value))
    save(dict(contract='cacheweft-final-review-v1',passed=False,measured_commit=MEASURED_COMMIT,status='qualification_in_progress'))
    try:value=qualify()
    except Exception as exc:
        value=dict(contract='cacheweft-final-review-v1',passed=False,measured_commit=MEASURED_COMMIT,error=type(exc).__name__+': '+str(exc).replace(str(ROOT),'<checkout>'))
    save(value)
    return value

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    value=write_qualification(a.output)
    print(json.dumps(dict(passed=value['passed'],tests=value.get('tests',{}).get('tests'),controls={k:v['corruption_controls']['rejected'] for k,v in value.get('records',{}).items()})))
    raise SystemExit(0 if value['passed'] else 1)
