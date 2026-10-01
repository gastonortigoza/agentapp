"""Controller-owned progress notes and PR backlinks, with durable reconciliation."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import re

import controller_gate
import github_ci
import github_pr
import github_publish
import integration_linear as linear
import integration_notion as notion
from integration_sync import SyncStore, RevisionConflict, digest
from local_control import LocalStore, require_evidence
from worker_lock import worker_lock

DATABASE = linear.ROOT / '.state/closeout.sqlite'


async def attach(store, creation, pr_url, title, remote):
    if not re.fullmatch(r'https://github.com/gastonortigoza/agentapp/pull/[1-9][0-9]*',pr_url):
        raise ValueError('Unauthorized PR destination')
    if creation['state']!='confirmed' or creation['paused']:
        raise RevisionConflict('Ticket not confirmed or paused')
    issue_id = creation['receipt']['remote_id']
    intent = {'service':'linear','resource':linear.PROJECT,'external_id':creation['id']+'-pr',
              'revision':'append-link-v1','action':'attach_pr',
              'payload':{'text':json.dumps({'issue':issue_id,'url':pr_url,'title':title},sort_keys=True)},
              'links':{**creation['intent']['links'],'pr':pr_url}}
    op = store.prepare(intent)
    with worker_lock(store.path,op) as acquired:
        if not acquired:
            raise RevisionConflict('Link already executing')
        row = store.get(op)
        if row['paused']:
            raise RevisionConflict('Link paused')
        issue = await remote.call('get_issue',{'id':issue_id})
        await linear.receipt(remote,creation['id'],creation['intent'],issue_id,issue=issue)
        def matches(value):
            links=[x for x in value.get('attachments',[]) if x.get('url')==pr_url]
            if len(links)>1 or (links and links[0].get('title')!=title):
                raise RevisionConflict('Ambiguous or externally edited PR link')
            return len(links)==1
        if matches(issue):
            if row['state']!='confirmed':
                store._confirm(op,intent,{'operation':op,'intent_hash':digest(intent),
                    'service':'linear','resource':linear.PROJECT,'remote_id':issue_id})
            return store.get(op)
        if row['state']=='confirmed':
            raise RevisionConflict('Previously confirmed link removed externally')
        if row['state'] in {'in_flight','uncertain'}:
            store._set(op,'uncertain','reconciliation_required')
            return store.get(op)
        args={'id':issue_id,'links':[{'url':pr_url,'title':title}]}
        remote.validate('save_issue',args)
        with store.transaction() as db:
            current=store._row(db,op)
            if current['version']!=row['version'] or current['paused']:
                raise RevisionConflict('Link authorization changed')
            store._event(db,current,'in_flight')
        try:
            await remote.call('save_issue',args)
            after=await remote.call('get_issue',{'id':issue_id})
            await linear.receipt(remote,creation['id'],creation['intent'],issue_id,issue=after)
            if not matches(after):
                raise RevisionConflict('Link not observed')
            store._confirm(op,intent,{'operation':op,'intent_hash':digest(intent),
                'service':'linear','resource':linear.PROJECT,'remote_id':issue_id})
        except Exception:
            store._set(op,'uncertain','reconciliation_required')
        return store.get(op)


async def append_note(external_id,text):
    notes=SyncStore(notion.DATABASE)
    async with notion.connection() as remote:
        op=await notion.prepare(notes,remote,external_id,text)
        row=await notion.execute(notes,op,remote)
        if row['state']!='confirmed':
            raise RevisionConflict('Evidence note requires reconciliation')
        return row['receipt']


def progress_sync(runs,run_id,observations):
    if not observations:
        return
    row=runs.get(run_id)
    contract=json.loads(row['manifest'])
    state=row['state']
    text='Estado del controlador: '+state+'\nEjecucion: '+run_id+'\n'
    text+='Politica Linear: report_only. El controlador no sobrescribe el estado humano del ticket.\n'
    for entry in observations:
        text+='Ticket '+entry['remote_id']+'; estado observado: '+str(entry['workflow_state'])+'\n'
    source=contract['pipeline'].get('source')
    if source:
        text+='Requisito remoto '+source['external_id']+'; revision '+source['revision']+'; contenido '+source['content_sha256']+'\n'
        text+='Entrada fijada como snapshot inmutable; cambios posteriores requieren otra ejecucion.\n'
    # Observation states can change on retry; persist the first payload and
    # never silently rebind an existing logical note to new human edits.
    external='progress-'+run_id+'-'+state
    notes=SyncStore(notion.DATABASE)
    op=digest(['notion',notion.PAGE,external,'append_note'])
    try:
        existing=notes.get(op)
        text=existing['intent']['payload']['text']
    except ValueError:
        pass
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda:asyncio.run(append_note(external,text))).result()


def verified_publication(job_id):
    controller_gate.require_green()
    job=github_publish.job_path(job_id)
    data=json.loads((job/'manifest.json').read_text(encoding='utf-8'))
    parent=data.get('controller_delivery',{}).get('parent_run_id')
    if not parent:
        raise ValueError('Publication has no controller run')
    runs=LocalStore(linear.ROOT/'.state/controller.sqlite')
    row=runs.get(parent)
    if row['state']!='delivered':
        raise ValueError('Run not delivered')
    with runs.transaction() as db:
        require_evidence(db,row,'delivered')
    if data['controller_delivery']['parent_manifest_hash']!=row['manifest_hash']:
        raise ValueError('Publication belongs to another contract')
    record=github_pr.status(job_id)
    if record['state']!='confirmed' or record['intent']['commit']!=data['commit']:
        raise ValueError('PR not confirmed for exact commit')
    adapter=github_pr.GitHubPR(data['repository'])
    _,evidence=github_pr.observed(adapter,record['intent'])
    if not evidence:
        raise ValueError('Current PR identity differs')
    ci=github_ci.inspect(job_id)
    return runs,row,data,evidence,ci


async def closeout(job_id):
    runs,row,data,pr,ci=verified_publication(job_id)
    contract=json.loads(row['manifest'])
    if linear.PROJECT not in contract['allowed_resources']['linear_projects']:
        raise ValueError('Run does not authorize Linear')
    tickets=SyncStore(linear.DATABASE);links=SyncStore(DATABASE)
    plan=runs.output(row['id'],'planner')
    results=[]
    async with linear.connection(write=True) as remote:
        await linear.verify_destination(remote)
        for index in range(1,len(plan['steps'])+1):
            op=digest(['linear',linear.PROJECT,f"PLAN-{row['id']}-{index}",'create_issue'])
            creation=tickets.get(op)
            if creation['state']!='confirmed' or creation['paused']:
                raise RevisionConflict('Ticket not confirmed or paused')
            result=await attach(links,creation,pr['url'],f"PR #{pr['number']} - controlador delivered; CI verificado",remote)
            if result['state']!='confirmed':
                raise RevisionConflict('PR link requires reconciliation')
            results.append(result['receipt'])
    text=('Cierre de trazabilidad por el controlador.\nEjecucion: '+row['id']+
          '\nPR: '+pr['url']+'\nCommit: '+data['commit']+
          '\nCI: https://github.com/'+data['repository']+'/actions/runs/'+str(ci['workflow_run_id'])+
          '\nCI verificado contra commit, workflow, emisor y jobs confiables.\n'+
          'Estado de ejecucion: delivered. Estado Linear conservado; evidencia separada, sin sobrescribir ediciones humanas.\n'+
          'Enlaces de regreso verificados por lectura. Sin merge ni despliegue.\n')
    for receipt in results:
        text+='Ticket: https://linear.app/agenta-pp/issue/'+receipt['remote_id']+'\n'
    note=await append_note('closeout-'+job_id,text)
    return {'job_id':job_id,'run_id':row['id'],'pr':pr,'ci':ci,'links':results,'notion':note,
            'linear_state_policy':'report_only','merged':False,'deployed':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    args=parser.parse_args(argv)
    try:
        result=asyncio.run(closeout(args.job_id))
        print(json.dumps(result,indent=2));return 0
    except Exception as exc:
        from integration_cli import failure_reason
        print(json.dumps({'status':'blocked','reason':failure_reason(exc)}));return 2


if __name__=='__main__':
    raise SystemExit(main())
