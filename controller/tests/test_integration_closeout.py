import asyncio
import copy
import pytest
import integration_closeout as c
import integration_linear as l
from integration_sync import SyncStore,digest,RevisionConflict


@pytest.fixture
def setup(tmp_path):
    tickets=SyncStore(tmp_path/'tickets.sqlite')
    intent=l.proposal({'external_id':'PLAN-run-1','title':'Step','description':'Preserve this',
        'requirement':'https://app.notion.com/p/example','run_id':'run','pr':'pending'})
    op=tickets.prepare(intent)
    tickets._confirm(op,intent,{'operation':op,'intent_hash':digest(intent),'service':'linear',
        'resource':l.PROJECT,'remote_id':'uuid-1'})
    fields=l.ticket_fields(op,intent)
    class Remote:
        def __init__(self):
            self.issue={'id':'uuid-1','projectId':l.PROJECT,'teamId':l.TEAM,
                'title':fields['title'],'description':fields['description'],
                'status':'Canceled','attachments':[{'url':'https://example.test/human','title':'Human link'}]}
            self.writes=0;self.lose=False;self.apply=True
        def validate(self,name,args):
            assert name=='save_issue' and set(args)=={'id','links'}
        async def call(self,name,args):
            if name=='get_issue':return copy.deepcopy(self.issue)
            self.validate(name,args);self.writes+=1
            if self.apply:self.issue['attachments'].extend(copy.deepcopy(args['links']))
            # Concurrent human state edit is preserved: no state in mutation.
            self.issue['status']='Backlog'
            if self.lose:raise TimeoutError()
            return copy.deepcopy(self.issue)
    return SyncStore(tmp_path/'links.sqlite'),tickets.get(op),Remote()


def attach(setup):
    store,creation,remote=setup
    return asyncio.run(c.attach(store,creation,'https://github.com/gastonortigoza/agentapp/pull/4','PR #4',remote))


def test_backlink_preserves_human_content_and_concurrent_state(setup):
    _,_,remote=setup;before=copy.deepcopy(remote.issue)
    assert attach(setup)['state']=='confirmed'
    assert attach(setup)['state']=='confirmed'
    assert remote.writes==1 and remote.issue['status']=='Backlog'
    assert remote.issue['description']==before['description']
    assert before['attachments'][0] in remote.issue['attachments']


def test_lost_response_reconciles_without_duplicate(setup):
    _,_,remote=setup;remote.lose=True
    assert attach(setup)['state']=='uncertain'
    assert attach(setup)['state']=='confirmed'
    assert remote.writes==1


def test_uncertain_absent_link_never_resends(setup):
    _,_,remote=setup;remote.lose=True;remote.apply=False
    assert attach(setup)['state']=='uncertain'
    assert attach(setup)['state']=='uncertain'
    assert remote.writes==1


def test_foreign_description_prevents_link_write(setup):
    _,_,remote=setup;remote.issue['description']+='human edit'
    with pytest.raises(RevisionConflict):attach(setup)
    assert remote.writes==0


def test_removed_confirmed_link_is_not_silently_recreated(setup):
    _,_,remote=setup;attach(setup);remote.issue['attachments']=[]
    with pytest.raises(RevisionConflict):attach(setup)
    assert remote.writes==1
