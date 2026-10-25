"""Fail-closed evidence contract and HTML generated from the same JSON."""
import copy
import html
import json
import math
import re

ARTIFACT_SHA="fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83"
INPUTS={'pyproject.toml','cacheweft/__init__.py','cacheweft/core.py','cacheweft/http.py',
        'tools/__init__.py','tools/cases.py','tools/reboot.py','tools/report.py','tools/unit.py','tools/validate.py',
        'tests/__init__.py','tests/fixture.py','tests/test_core.py'}


def verify(v):
    def need(value,message):
        if not value:raise ValueError(message)
    def number(value,positive=False):
        return type(value) in (int,float) and math.isfinite(value) and (value>0 if positive else value>=0)
    def integer(value,positive=False):
        return type(value) is int and (value>0 if positive else value>=0)
    def sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None
    need(v.get('contract')=='cacheweft-exploration-review-v3','Contract mismatch')
    need(v.get('passed') is True and not v.get('error') and not v.get('verification_error') and not v.get('cleanup_error'),'Not cleanly passed')
    need(v.get('source_unchanged') is True and v.get('host_mounts')=='none','Source/mount configuration')
    expected={'cacheweft/__init__.py','cacheweft/core.py','cacheweft/http.py'}
    need(set(v['source'])==INPUTS,'Missing frozen inputs')
    need(set(v['installed_modules'])==expected and all(sha(h) for h in v['source'].values()) and sha(v['wheel_sha256']),'Missing source provenance')
    need(v['installed_modules']==v['native']['modules']==v['linux']['modules'],'Runtime provenance mismatch')
    need(v['installed_modules']=={k:h for k,h in v['source'].items() if k.startswith('cacheweft/')},'Runtime/source mismatch')
    n,l=v['native'],v['linux']
    need(n['platform']=='Darwin' and l['platform']=='Linux','Platform mismatch')
    need(n['test_ids']==l['test_ids'] and len(set(n['test_ids']))==n['tests']==l['tests']==42,'Test cohort mismatch')
    for t in (n,l):
        need(t['passed'] is True and t['errors']==t['failures']==t['skipped']==0 and number(t['seconds'],True),'Tests not clean')
    stages=v['stages'];need(len({x['name'] for x in stages})==len(stages),'Duplicate stages')
    for stage in stages:
        need(type(stage['exit_code']) is int and stage['exit_code']==0 and stage['timed_out'] is False and number(stage['seconds']),'Stage failure')
    for name in ('initial','create','dependencies','upload','install','provenance','native','native-cases','linux','linux-read','linux-cases','linux-cases-read','reboot-prepare','reboot-prepared-read','schedule-reboot','reboot-finish','reboot-finished-read','delete','final'):
        need(name in [s['name'] for s in stages],'Missing stage '+name)
    need(all(v['cleanup'][key] is True for key in ('vm_deleted','disk_removed','preexisting_unchanged')),'Cleanup incomplete')
    boot=v['reboot'];before=boot['before']
    need(boot['boot_changed'] is True and boot['catalog_unchanged_before_recovery'] is True and boot['recovered_correct'] is True,'Reboot invalid')
    need(len(boot['boot_id_after'])==len(before['boot_id'])==36 and boot['boot_id_after']!=before['boot_id'],'Boot identity unchanged')
    need(sha(before['catalog_sha256']) and boot['catalog_sha256_before_recovery']==before['catalog_sha256'],'Catalog witness differs')
    need(boot['stage_sha256_before_recovery']==before['stage_sha256']==[ARTIFACT_SHA],'Staging witness differs')
    need(boot['states_before_recovery']==before['states'],'Pending state not preserved')
    objects,requests=before['states']['objects'],before['states']['requests']
    need(len(objects)==len(requests)==1,'Boot cohort mismatch')
    obj,req=objects[0],requests[0]
    need(obj[0]==ARTIFACT_SHA and obj[2:] == ['PUBLISHING',1048576] and req==['reboot_request',obj[1],'WAITING'],'Not an unfinished publication')
    need(before['target_absent'] is True and before['crash_exit']==73 and boot['normal_reboot'] is True and boot['power_loss_tested'] is False,'Boot scope mismatch')
    recovered=boot['recovered_request']
    need(recovered['id']==req[0] and recovered['generation']==obj[1] and recovered['digest']==obj[0] and recovered['state']==recovered['result']=='SUCCEEDED','Wrong recovered request')
    faults=v['linux_cases']['storage'];need(faults['mount_removed'] is True,'Mount left')
    need([x['mode'] for x in faults['cases']]==['enospc','readonly','sqlite_full_admission','sqlite_full_publish'],'Missing storage cases')
    for x in faults['cases']:
        need(x['repair_correct'] is True and x['rejected'] is True,'Fault repair/admission failed')
        if x['mode']=='sqlite_full_admission':
            need(x['sqlite_errorcode']==13 and x['degraded'] is True,'Not admission SQLITE_FULL')
        else:
            need(x['published_before_repair'] is False,'Fault published')
            codes=[(e.get('errno'),e.get('sqlite_errorcode')) for e in x['actual_errors']]
            if x['mode']=='sqlite_full_publish':
                need(any(b==13 for a,b in codes) and x['degraded'] is True,'Not publish SQLITE_FULL')
                need(x['state_before_repair']==dict(object='VERIFYING',request='WAITING') and x['repair_origin_requests']==0,'Pending repair differs')
            else:
                need(any((a==28 or b==13) if x['mode']=='enospc' else (a==30 or (b is not None and b&255==8)) for a,b in codes),'Wrong actual storage error')
    for env,platform,kind in (('native_cases','Darwin','peak_RSS'),('linux_cases','Linux','current_RSS')):
        m=v[env]['measurements']
        need(m['environment']==platform and m['memory_kind']==kind,'Metric scope mismatch')
        need([t['mode'] for t in m['trials']]==['cold','resume','coalesced'],'Missing/duplicate modes')
        for t in m['trials']:
            need(t['correct'] is True and number(t['download_seconds'],True) and number(t['read_seconds'],True),'Invalid timing/correctness')
            need(t['size']==t['origin_payload_bytes']==1048576,'Unexpected payload accounting')
            need(integer(t['process_rss_or_peak_bytes'],True) and integer(t['root_allocated_bytes'],True),'Invalid memory/allocation')
            u=t['usage']
            need(u['payload_bytes']==1048576 and u['remaining_reserved']==0 and integer(u['allocated_payload_bytes'],True) and integer(u['free_bytes']) and integer(u['budget']) and u['budget']>=1048576,'Invalid usage')
            need(t['root_allocated_bytes']>=u['allocated_payload_bytes'],'Allocation scopes inverted')
            coalesced=t['mode']=='coalesced';ids=['r','r1','r2','r3'] if coalesced else ['r']
            need(t['request_count']==len(ids) and [o['id'] for o in t['subscriber_outcomes']]==ids,'Request accounting')
            need(all(o['digest']==ARTIFACT_SHA and o['state']==o['result']=='SUCCEEDED' for o in t['subscriber_outcomes']),'Subscriber not successful')
            if coalesced:
                waiting=t['subscribers_before_release']
                need([w['id'] for w in waiting]==ids and all(w['state']=='WAITING' and w['digest']==ARTIFACT_SHA for w in waiting),'Not concurrent subscribers')
                need(len({w['generation'] for w in waiting+t['subscriber_outcomes']})==1,'Subscriber generation differs')
            else:need(t['subscribers_before_release']==[],'Unexpected barrier cohort')
            ranges=[None,'bytes=131072-'] if t['mode']=='resume' else [None]
            need(t['actual_origin_requests']==len(ranges) and t['request_ranges']==ranges,'Origin request/range mismatch')
    need(v['limits']==dict(power_loss_tested=False,wan_tested=False,deployed_service_tested=False,long_term_tested=False),'Limit changed')


def controls(value):
    changes=[lambda v:v['native'].update(skipped=1),lambda v:v['linux'].update(tests=0),
             lambda v:v['stages'][0].update(timed_out=True),lambda v:v['stages'][0].update(exit_code=1),
             lambda v:v['installed_modules'].clear(),lambda v:v['reboot'].update(catalog_unchanged_before_recovery=False),
             lambda v:v['reboot'].update(boot_changed=False),lambda v:v['cleanup'].update(disk_removed=False),
             lambda v:v['linux_cases']['storage']['cases'][2].update(sqlite_errorcode=0),
             lambda v:v['linux_cases']['measurements']['trials'][2].update(actual_origin_requests=4),
             lambda v:v['reboot'].update(boot_id_after=v['reboot']['before']['boot_id']),
             lambda v:v['reboot']['states_before_recovery']['objects'][0].__setitem__(2,'READY'),
             lambda v:v['source'].update({'cacheweft/core.py':'0'*64}),
             lambda v:v['native_cases']['measurements']['trials'][1].update(origin_payload_bytes=0),
             lambda v:v['linux_cases']['measurements']['trials'][0].update(download_seconds=float('inf')),
             lambda v:v['linux_cases']['measurements']['trials'][0].update(download_seconds=float('nan')),
             lambda v:v['native_cases']['measurements']['trials'][0].update(process_rss_or_peak_bytes=-1),
             lambda v:v['native_cases']['measurements']['trials'][0].update(root_allocated_bytes=-1),
             lambda v:v['native_cases']['measurements']['trials'][0].update(usage={}),
             lambda v:v['linux_cases']['measurements'].update(trials=[copy.deepcopy(v['linux_cases']['measurements']['trials'][0]) for _ in range(3)]),
             lambda v:v.update(stages=[s for s in v['stages'] if s['name']!='native-cases']),
             lambda v:(v.update(installed_modules={}),v['native'].update(modules={}),v['linux'].update(modules={}),v.update(source={k:h for k,h in v['source'].items() if not k.startswith('cacheweft/')})),
             lambda v:v['reboot'].update(catalog_sha256_before_recovery='0'*64),
             lambda v:v['linux_cases']['storage']['cases'][3].update(published_before_repair=True),
             lambda v:v['linux_cases']['measurements']['trials'][2]['subscribers_before_release'][1].update(state='SUCCEEDED'),
             lambda v:v['source'].pop('tests/test_core.py')]
    for change in changes:
        v=copy.deepcopy(value);change(v)
        try:verify(v)
        except (ValueError,KeyError,TypeError):continue
        raise ValueError('Corrupted evidence accepted')
    return dict(rejected=len(changes),all_rejected=True)


def render(v):
    title='Cacheweft exploration evidence'
    return '<!doctype html><html lang="en"><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:20px}</style><h1>'+title+'</h1><p>Passed: '+str(v.get('passed',False))+'</p><p>Exploration core only. Linux VM metrics are separate from native macOS. No power-loss, WAN, long-term or deployed-service qualification.</p><pre>'+html.escape(json.dumps(v,indent=2,allow_nan=False))+'</pre></html>\n'
