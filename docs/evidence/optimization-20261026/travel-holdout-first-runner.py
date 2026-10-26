"""One-shot frozen offline holdout over real, isolated business HTTP.

No provider SDK, Docker, SQL, interception, cleanup, or automatic sample retry.
Inputs/rules are authored before execution and protected by the freeze manifest.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_PACKAGE=ROOT/'docs/evidence/optimization-20261026/travel-holdout-frozen.json'
DEFAULT_MANIFEST=ROOT/'docs/evidence/optimization-20261026/travel-holdout-freeze-manifest.json'


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def redact(value):
    sensitive={'access_token','refresh_token','token','password','hashed_password','secret','api_key','authorization','cookie','set-cookie'}
    if isinstance(value,dict):return {key:'[redacted]' if key.casefold() in sensitive else redact(item) for key,item in value.items()}
    if isinstance(value,list):return [redact(item) for item in value]
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*_):return None


class Client:
    def __init__(self,base_url,trace,on_trace=lambda:None):
        parsed=urlsplit(base_url)
        host=parsed.hostname or ''
        try:loopback=ipaddress.ip_address(host).is_loopback
        except ValueError:loopback=host.casefold()=='localhost'
        if not loopback or parsed.scheme not in {'http','https'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
            raise ValueError('Only a loopback isolated fixture base URL is allowed')
        self.base=base_url.rstrip('/');self.trace=trace;self.token=None;self.on_trace=on_trace
        self.opener=build_opener(ProxyHandler({}),NoRedirect())

    def call(self,method,path,body=None,headers=None,allowed=(200,)):
        request_headers={'Accept':'application/json',**(headers or {})}
        if self.token:request_headers['Authorization']='Bearer '+self.token
        if body is not None:request_headers['Content-Type']='application/json'
        payload=None if body is None else json.dumps(body,ensure_ascii=False).encode()
        request=Request(self.base+path,data=payload,headers=request_headers,method=method)
        started=time.perf_counter()
        try:
            with self.opener.open(request,timeout=60) as response:status=response.status;raw=response.read(8*1024*1024+1)
        except HTTPError as error:
            status=error.code;raw=error.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise RuntimeError('HTTP artifact exceeded bounded response size')
        try:response=json.loads(raw)
        except ValueError:response={'non_json_response':True,'body_sha256':hashlib.sha256(raw).hexdigest()}
        entry={'method':method,'path':path,'status':status,'elapsed_seconds':round(time.perf_counter()-started,3),'response':redact(response)}
        if body is not None:entry['request']=redact(body)
        self.trace.append(entry)
        self.on_trace()
        if status not in allowed:raise RuntimeError(f'Unexpected HTTP status {status} for {method} {path}')
        return status,response


def instant(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def rule(result,identifier,passed,detail):
    result['rules'].append({'id':identifier,'passed':bool(passed),'detail':detail})


def days(plan):
    return {day['day_index']:day for day in plan.get('days',[])}


def day_projection(day):
    return {key:day.get(key) for key in ('day_index','date','city','attractions','meals','hotel','timeline','feasibility','schedule_stale')}


def activity(day):
    values=day.get('attractions',[])
    if not values or not values[0].get('activity_id') or not values[0].get('poi_id'):
        raise RuntimeError('Frozen prerequisite failed: first real attraction activity/POI ID absent')
    return values[0]


def explicit_feasibility(plan,quality):
    for day in plan.get('days',[]):
        feasibility=day.get('feasibility',{})
        status=feasibility.get('status')
        if status=='feasible':
            if day.get('schedule_stale') or feasibility.get('unknown') or feasibility.get('conflicts'):return False
        elif status in {'unverified','infeasible','not_found'}:
            if not any((feasibility.get('unknown'),feasibility.get('conflicts'),quality.get('issues'),quality.get('warnings'),quality.get('data_gaps'))):return False
        else:return False
    return True


def get_input(client,record_id):
    return client.call('GET',f'/api/trips/{record_id}/planning-input')[1]['data']


def put_input(client,record_id,snapshot,new_input):
    return client.call('PUT',f'/api/trips/{record_id}/planning-input',
        {'planning_revision':snapshot['planning_revision'],'planning_input':new_input},
        {'If-Match':str(snapshot['trip_version'])})[1]['data']


def state(client,record_id,snapshot,activity_id,body,allowed=(200,)):
    return client.call('POST',f'/api/trips/{record_id}/activities/{activity_id}/state',
        {**body,'planning_revision':snapshot['planning_revision']},
        {'If-Match':str(snapshot['trip_version'])},allowed)


def generate(client,case,result):
    _,created=client.call('POST','/api/trip/tasks',case['request'],{'Idempotency-Key':uuid4().hex},allowed=(202,))
    task_id=created['data']['id'];result['task_id']=task_id
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        _,response=client.call('GET',f'/api/trip/tasks/{task_id}')
        task=response['data']
        if task.get('status') in {'succeeded','needs_attention','failed','cancelled'}:break
        time.sleep(1.5)
    else:raise RuntimeError('Frozen task deadline exceeded; no sample retry allowed')
    result['task_status']=task.get('status');result['usage']=task.get('usage')
    okay=task.get('status') in {'succeeded','needs_attention'} and task.get('result',{}).get('saved') is True and bool(task.get('result',{}).get('id'))
    rule(result,'C02',okay,'Durable task terminal state and persisted record, not HTTP 200 alone')
    if not okay:raise RuntimeError('Frozen prerequisite failed: durable task did not persist a plan')
    saved=task['result'];result['record_id']=saved['id']
    result['initial_result']=deepcopy(saved)
    plan=saved['data'];by_day=days(plan)
    cities=len(plan.get('days',[]))==2 and set(by_day)=={0,1} and by_day[0].get('city')=='北京' and by_day[1].get('city')=='上海'
    for index,city_code in ((0,'beijing'),(1,'shanghai')):
        for attraction in by_day.get(index,{}).get('attractions',[]):
            cities=cities and attraction.get('poi_id','').startswith('fixture-'+city_code+'-')
    rule(result,'C01',cities,'Exactly two expected day cities; fixture attraction IDs stay within each synthetic city')
    rule(result,'C03',explicit_feasibility(plan,saved.get('quality',{})),'Feasible cannot coexist with stale/unknown/conflicts; conservative results need explicit reasons')
    rule(result,'C04',True,'Complete returned plan/quality/usage retained; structural evaluation only, human review pending')
    return saved['id'],plan


def execute_a(client,case,result,record_id,initial):
    before=get_input(client,record_id);result['before_fact']=deepcopy(before)
    initial_rail=case['request']['planning_input']['commitments'][0]
    railway=next((item for item in before['planning_input'].get('commitments',[]) if str(item.get('id'))==initial_rail['id']),{})
    fields=('id','category','status','time_mode','origin_city','destination_city','origin_name','destination_name','origin_location','destination_location')
    conserved=all(railway.get(key)==initial_rail.get(key) for key in fields)
    try:conserved=conserved and all(instant(railway[key])==instant(initial_rail[key]) for key in ('start_at','end_at'))
    except (KeyError,ValueError):conserved=False
    rule(result,'A01',conserved,'Fixed railway input interval and endpoints are conserved')
    expected_buffer=case['request']['planning_input']['transport_buffers'][0]
    buffer=next((item for item in before['planning_input'].get('transport_buffers',[]) if item.get('commitment_id')==expected_buffer['commitment_id']),{})
    rule(result,'A02',all(buffer.get(key)==value for key,value in expected_buffer.items()),'Exact user-declared buffer metadata is preserved')
    second=days(initial)[1]
    if second.get('feasibility',{}).get('status')=='feasible':
        rows=second.get('timeline',[])
        def matches(row):
            try:return row.get('kind')=='transport' and instant(row['start_at'])==instant(initial_rail['start_at']) and instant(row['end_at'])==instant(initial_rail['end_at'])
            except (KeyError,ValueError):return False
        start,end=instant(initial_rail['start_at']),instant(initial_rail['end_at'])
        tourism_ids={item.get('activity_id') for item in second.get('attractions',[])}
        overlaps=False
        for row in rows:
            if row.get('activity_id') in tourism_ids:
                try:overlaps=overlaps or instant(row['start_at'])<end and instant(row['end_at'])>start
                except (KeyError,ValueError):overlaps=True
        timetable=any(matches(row) for row in rows) and not overlaps
    else:timetable=explicit_feasibility(initial,result['initial_result'].get('quality',{}))
    rule(result,'A05',timetable,'Feasible must conserve fixed transport in timeline and avoid tourism overlap; explicit conservative result allowed')
    fact=deepcopy(case['fact_to_add']);fact['poi_id']=activity(days(before['plan'])[1])['poi_id']
    changed=deepcopy(before['planning_input']);changed['reservation_facts'].append(fact)
    put_input(client,record_id,before,changed)
    after=get_input(client,record_id);result['after_fact']=deepcopy(after)
    persisted=next((item for item in after['planning_input'].get('reservation_facts',[]) if item.get('poi_id')==fact['poi_id'] and item.get('date')==fact['date']),{})
    rule(result,'A03',all(persisted.get(key)==value for key,value in fact.items()),'User reservation fact persisted verbatim without official authority')
    earlier,later=days(before['plan']),days(after['plan'])
    rule(result,'A04',later[1].get('schedule_stale') is True and day_projection(earlier[0])==day_projection(later[0]),'Shanghai fact invalidates Shanghai only; Beijing semantic projection unchanged')


def execute_b(client,case,result,record_id,_initial):
    snapshot=get_input(client,record_id)
    locked=activity(days(snapshot['plan'])[0]);target=activity(days(snapshot['plan'])[1])
    state(client,record_id,snapshot,locked['activity_id'],{'locked':True,'lock_mode':'content'})
    before=get_input(client,record_id);result['after_content_lock']=deepcopy(before)
    changed=deepcopy(before['planning_input'])
    changed['hotel_stays']=[item for item in changed['hotel_stays'] if item.get('day_index')!=1]+[deepcopy(case['hotel_replacement'])]
    put_input(client,record_id,before,changed)
    after=get_input(client,record_id);result['after_hotel_edit']=deepcopy(after)
    def has_lock(snapshot):return any(item.get('activity_id')==locked['activity_id'] and item.get('mode')=='content' for item in snapshot['planning_input']['locks'])
    retained=next((item for item in days(after['plan'])[0]['attractions'] if item.get('activity_id')==locked['activity_id']),None)
    rule(result,'B01',has_lock(before) and has_lock(after) and retained==locked,'Accepted content lock and exact attraction content survive the other day edit')
    only_hotel=deepcopy(after['planning_input']);only_hotel['hotel_stays']=deepcopy(before['planning_input']['hotel_stays'])
    local=(only_hotel==before['planning_input'] and after['planning_input']['hotel_stays']==changed['hotel_stays'] and
        days(after['plan'])[1].get('schedule_stale') is True and day_projection(days(before['plan'])[0])==day_projection(days(after['plan'])[0]))
    rule(result,'B02',local,'Only hotel input changes; Shanghai stale, Beijing projection and existing protection untouched')
    specific=True;atomic=True
    for body in ({'locked':True,'lock_mode':'time'},{'completed':True}):
        checkpoint=get_input(client,record_id)
        status,response=state(client,record_id,checkpoint,target['activity_id'],body,allowed=(200,409,422))
        specific=specific and status==409 and response.get('code')=='SCHEDULE_STALE'
        latest=get_input(client,record_id)
        atomic=atomic and latest==checkpoint
        result.setdefault('rejected_state_snapshots',[]).append({'operation':body,'before':checkpoint,'status':status,'response':response,'after':latest})
    rule(result,'B03',specific,'Both state writes specifically reject stale data, using current revisions')
    rule(result,'B04',atomic,'Every rejected write preserves authoritative version, input and plan exactly')


def run(base_url,package_path,manifest_path,output):
    Client(base_url,[])  # Reject credential-bearing/external URLs before reports.
    if output.exists():raise ValueError('Result must be a new file; never overwrite the first held-out result')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if digest(package_path)!=manifest['input_and_rules_sha256']:raise ValueError('Frozen input/rules checksum mismatch')
    package=json.loads(package_path.read_text(encoding='utf-8'))
    if len(package['cases'])!=2 or package['external_paid_calls_allowed']!=0:raise ValueError('Unexpected frozen sample or budget contract')
    frozen_sources={**manifest['application_source_sha256'],**manifest.get('supplemental_application_source_sha256',{})}
    source_hashes={name:digest(ROOT/name) for name in frozen_sources}
    drift=[name for name,value in source_hashes.items() if value!=frozen_sources[name]]
    report={'schema_version':1,'started_at':datetime.now(timezone.utc).isoformat(),'frozen_at':package['frozen_at'],
        'classification':package['classification'],'base_url':base_url,'passed':False,'http_executed':False,
        'frozen_manifest_sha256':digest(manifest_path),'frozen_inputs_sha256':digest(package_path),'runner_sha256':digest(Path(__file__)),
        'application_source_sha256':source_hashes,'source_drift':drift,'cases':[],
        'paid_external_calls':0,'provider_mode':'offline_fixture','human_blind_review':'pending',
        'boundary':'Synthetic suppliers with real business HTTP/Java/PG/Agent. No live-model or human accuracy proof. Runtime image/network binding remains coordinator evidence; fixture marker is required but not an image attestation.',
        'retention':'No cleanup performed; synthetic accounts/trips and first result are retained. A failed sample becomes development and may not be silently replaced.'}
    output.parent.mkdir(parents=True,exist_ok=True)
    def save():output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    # Create the initial result exclusively so a second invocation cannot claim
    # an untouched holdout by replacing a previous failure report.
    with output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    try:
        if drift:raise RuntimeError('Application source changed after freeze; no HTTP execution allowed')
        marker_trace=[];probe=Client(base_url,marker_trace,save)
        report['fixture_preflight']=marker_trace
        report['http_executed']=True
        _,marker=probe.call('GET','/api/validation/fixture')
        if marker.get('offline_fixture') is not True:raise RuntimeError('Target does not provide the required isolated fixture marker')
        claim_path=manifest_path.with_name('travel-holdout-first-execution.json')
        claim={'started_at':report['started_at'],'frozen_inputs_sha256':report['frozen_inputs_sha256'],
               'runner_sha256':report['runner_sha256'],'output':str(output.resolve()),
               'policy':'First sample execution reserved; never remove to rerun for a better result.'}
        try:
            with claim_path.open('x',encoding='utf-8') as stream:json.dump(claim,stream,indent=2)
        except FileExistsError:
            raise RuntimeError('The frozen holdout was already reserved/executed; preserve its original result') from None
        report['first_execution_claim']=str(claim_path)
        # No account or business mutation is attempted until fixture proof.
        for case in package['cases']:
            result={'id':case['id'],'rules':[],'http':[],'passed':False}
            report['cases'].append(result);save()
            try:
                client=Client(base_url,result['http'],save)
                username='ho_'+case['id'][-1].lower()+'_'+uuid4().hex[:20]
                _,auth=client.call('POST','/api/auth/register',{'username':username,'password':secrets.token_urlsafe(24)+'A7!'})
                client.token=auth['access_token'];result['synthetic_username']=username
                record_id,plan=generate(client,case,result)
                (execute_a if case['id']=='HOLDOUT-A' else execute_b)(client,case,result,record_id,plan)
            except Exception as error:
                result['execution_error_type']=type(error).__name__
                result['execution_error']=str(error) if isinstance(error,RuntimeError) else 'Unexpected contract/transport error; inspect bounded HTTP artifacts'
            expected={item['id'] for item in package['common_rules']+case['rules']}
            present={item['id'] for item in result['rules']}
            for missing in sorted(expected-present):rule(result,missing,False,'Not reached; prerequisite or HTTP contract failure is retained, not skipped')
            result['passed']=all(item['passed'] for item in result['rules']) and 'execution_error_type' not in result
            result['future_classification']='heldout_completed_once' if result['passed'] else 'failed_holdout_now_development_do_not_replace'
            save()
        report['passed']=len(report['cases'])==2 and all(case['passed'] for case in report['cases'])
    except Exception as error:
        report['preflight_error_type']=type(error).__name__
        report['preflight_error']=str(error) if isinstance(error,RuntimeError) else 'Invalid local target/freeze configuration'
    report['completed_at']=datetime.now(timezone.utc).isoformat()
    report['rules_passed']=sum(rule['passed'] for case in report['cases'] for rule in case['rules'])
    report['rules_total']=sum(len(case['rules']) for case in report['cases'])
    save()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True,help='Actual loopback isolated frontend/business fixture origin')
    parser.add_argument('--package',type=Path,default=DEFAULT_PACKAGE)
    parser.add_argument('--manifest',type=Path,default=DEFAULT_MANIFEST)
    parser.add_argument('--output',type=Path,required=True,help='Fresh immutable-first-run result JSON path')
    args=parser.parse_args()
    result=run(args.base_url,args.package,args.manifest,args.output)
    print(json.dumps({'passed':result['passed'],'output':str(args.output),'cases':len(result['cases']),
        'rules_passed':result['rules_passed'],'rules_total':result['rules_total'],'paid_external_calls':0,'human_blind_review':'pending'}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
