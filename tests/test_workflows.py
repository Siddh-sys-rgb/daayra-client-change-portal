import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor, wait
from threading import Barrier, BrokenBarrierError
import pytest
import domain
from core import Problem,connect
from conftest import login,post,user

DESIGNER=('designer@daayra.demo','Studio@2026')
DEV=('dev@daayra.demo','Client@2026')
NISHA=('nisha@daayra.demo','Client@2026')
BASE={'project_id':1,'title':'Add print labels','description':'Three approved label designs as print-ready PDFs.','amount':'1234.56','days':2}



def run_race(barrier,worker,values,timeout=35):
    """Fail together if preparation fails; surface that original failure promptly."""
    def guarded(value):
        try:
            return worker(value)
        except BaseException:
            barrier.abort()
            raise
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(guarded,value) for value in values]
        done,pending=wait(futures,timeout=timeout)
        if pending:
            barrier.abort()
            for future in pending:future.cancel()
            raise TimeoutError('Concurrent workflow did not finish within the test deadline.')
        errors=[future.exception() for future in futures if future.exception() is not None]
        if errors:
            # The peer's broken barrier is a consequence, not the setup failure.
            raise next((error for error in errors if not isinstance(error,BrokenBarrierError)),errors[0])
        return [future.result() for future in futures]


def change(client,request_id):return client.get(f'/api/requests/{request_id}').json['change']


def upload(client,request_id,revision,filename='brief.txt',content=b'Packaging sizes: 250g and 500g'):
    token=client.get('/api/session').json['csrf_token']
    return client.post(f'/api/requests/{request_id}/attachments',data={'revision':str(revision),'file':(io.BytesIO(content),filename)},headers={'X-CSRF-Token':token},content_type='multipart/form-data')


def test_boot_security_headers_and_unique_cookie(client):
    assert client.get('/api/health').json['status']=='ok'
    page=client.get('/');assert page.status_code==200 and b'daayra' in page.data
    assert "script-src 'self'" in page.headers['Content-Security-Policy']
    assert page.headers['X-Content-Type-Options']=='nosniff'
    assert 'daayra_session=' in client.get('/api/session').headers['Set-Cookie']


@pytest.mark.parametrize('path',['/api/projects','/api/requests/1','/api/requests/1/events','/api/attachments/1'])
def test_anonymous_cannot_read(client,path):assert client.get(path).status_code==401


@pytest.mark.parametrize('path',['/api/login','/api/logout','/api/requests','/api/requests/1/revise','/api/requests/1/approve','/api/requests/1/attachments'])
def test_csrf_on_every_mutation(client,path):assert client.post(path,json={}).status_code==403


def test_token_rotation_logout_and_cross_origin(client):
    old=client.get('/api/session').json['csrf_token'];signed=login(client,*DEV)
    assert old!=signed['csrf_token']
    assert client.post('/api/logout',json={},headers={'X-CSRF-Token':old}).status_code==403
    assert client.post('/api/logout',json={},headers={'X-CSRF-Token':signed['csrf_token'],'Origin':'https://other.example'}).status_code==403
    assert post(client,'/api/logout').status_code==200
    assert client.get('/api/projects').status_code==401


def test_throttled_login_does_not_grant_session(client):
    for _ in range(8):assert post(client,'/api/login',{'email':DEV[0],'password':'wrong'}).status_code==401
    assert post(client,'/api/login',{'email':DEV[0],'password':DEV[1]}).status_code==429
    assert client.get('/api/projects').status_code==401


@pytest.mark.parametrize('email,password',[('missing@daayra.demo','wrong'),(123,'test'),(DEV[0],None),(DEV[0],'')])
def test_invalid_login(client,email,password):assert post(client,'/api/login',{'email':email,'password':password}).status_code in (400,401)


def test_clients_only_see_own_project(client):
    login(client,*DEV);projects=client.get('/api/projects').json['projects'];assert [p['id'] for p in projects]==[1]
    assert client.get('/api/requests/3').status_code==404
    assert client.get('/api/requests/3/events').status_code==404
    assert post(client,'/api/requests',{**BASE,'project_id':2}).status_code==404
    login(client,*DESIGNER);assert len(client.get('/api/projects').json['projects'])==2


def test_seed_restart_and_empty_start(app,tmp_path):
    from app import create_app
    restart=create_app({'TESTING':True,'DATA_DIR':app.config['DATA_DIR'],'DEMO':True});assert restart.secret_key==app.secret_key
    db=connect(app.config['DB_PATH']);assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==2
    assert db.execute('SELECT password_hash FROM users WHERE id=1').fetchone()[0].startswith('scrypt:');db.close()
    empty=create_app({'TESTING':True,'DATA_DIR':str(tmp_path/'empty'),'DEMO':False});assert empty.test_client().get('/api/health').json['demo'] is False


def test_create_exact_paise_and_immutable_revision(client,app):
    login(client,*DEV);response=post(client,'/api/requests',BASE);assert response.status_code==201
    row=response.json['change'];assert row['status']=='draft' and row['current']['delta_paise']==123456
    assert row['current_version']==1 and row['accepted_version'] is None
    assert post(client,f"/api/requests/{row['id']}/revise",{**BASE,'amount':'1234.57','revision':1}).status_code==200
    updated=change(client,row['id']);assert updated['current_version']==2 and updated['revision']==2
    assert updated['versions'][0]['delta_paise']==123456 and updated['versions'][1]['delta_paise']==123457
    db=connect(app.config['DB_PATH'])
    with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE versions SET title=? WHERE request_id=?',('Tampered',row['id']))
    with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM versions WHERE request_id=?',(row['id'],))
    with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE projects SET agreed_paise=1 WHERE id=1')
    db.close()


@pytest.mark.parametrize('amount',['0','0.01','10.10','-10.25','1000000.00','-1000000.00'])
def test_exact_decimal_amounts(amount):
    from decimal import Decimal
    assert domain.money(amount)==int(Decimal(amount)*100)


@pytest.mark.parametrize('amount',[None,100,'1.234','1e3','NaN','Infinity','01.00','+12.00',' 12.00','1000000.01','-1000000.01','1,000','١٢.00'])
def test_invalid_money(client,amount):
    login(client,*DEV)
    assert post(client,'/api/requests',{**BASE,'amount':amount}).status_code==400


@pytest.mark.parametrize('overrides',[
 {'title':''},{'title':'a'*81},{'title':123},{'title':'bad\x00title'},
 {'description':'short'},{'description':'x'*1801},{'description':None},
 {'days':True},{'days':1.5},{'days':'2'},{'days':91},{'days':-91},
 {'project_id':True},{'project_id':0},
])
def test_invalid_proposals_do_not_create_partial_state(client,app,overrides):
    login(client,*DEV);assert post(client,'/api/requests',{**BASE,**overrides}).status_code==400
    db=connect(app.config['DB_PATH']);assert db.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==3;assert db.execute('SELECT COUNT(*) FROM versions').fetchone()[0]==3;db.close()


def test_designer_cannot_create_client_draft(client):
    login(client,*DESIGNER);assert post(client,'/api/requests',BASE).status_code==403


def test_two_party_approval_pins_current_version_and_updates_plan(client):
    login(client,*DEV);before=client.get('/api/projects').json['projects'][0]
    assert before['current_paise']==2800000
    # Existing request 1 already contains the client's version-1 approval.
    assert post(client,'/api/requests/1/approve',{'revision':1}).status_code==409
    login(client,*DESIGNER);r=post(client,'/api/requests/1/approve',{'revision':1});assert r.status_code==200
    row=r.json['change'];assert row['accepted_version']==row['client_approved_version']==row['designer_approved_version']==1
    p=client.get('/api/projects').json['projects'][0];assert p['current_paise']==3250000 and p['current_days']==17
    assert post(client,'/api/requests/1/approve',{'revision':2}).status_code==409
    assert post(client,'/api/requests/1/revise',{**BASE,'revision':2}).status_code==409


def test_designer_counterproposal_requires_fresh_client_consent(client):
    login(client,*DESIGNER)
    r=post(client,'/api/requests/1/revise',{**BASE,'title':'Festival labels and print check','amount':'5500.00','days':4,'revision':1})
    assert r.status_code==200
    row=r.json['change'];assert row['status']=='awaiting_client' and row['current_version']==2
    assert row['client_approved_version'] is None and row['designer_approved_version']==2
    assert row['versions'][0]['delta_paise']==450000
    assert post(client,'/api/requests/1/approve',{'revision':2}).status_code==409
    login(client,*DEV)
    assert post(client,'/api/requests/1/approve',{'revision':1}).status_code==409
    row=post(client,'/api/requests/1/approve',{'revision':2}).json['change'];assert row['accepted_version']==2 and row['status']=='accepted'
    assert post(client,'/api/requests/1/revise',{**BASE,'revision':3}).status_code==409


def test_client_draft_submission_and_designer_rejection(client):
    login(client,*DEV)
    assert post(client,'/api/requests/2/submit',{'revision':1}).status_code==200
    assert post(client,'/api/requests/2/revise',{**BASE,'revision':2}).status_code==409
    login(client,*DESIGNER)
    assert post(client,'/api/requests/2/reject',{'revision':2,'reason':'Needs an agreed content list first.'}).status_code==200
    row=change(client,2);assert row['status']=='rejected' and row['accepted_version'] is None
    assert client.get('/api/projects').json['projects'][0]['current_paise']==2800000
    assert post(client,'/api/requests/2/approve',{'revision':3}).status_code==409


def test_client_can_reject_counterproposal(client):
    login(client,*DESIGNER);assert post(client,'/api/requests/1/revise',{**BASE,'revision':1}).status_code==200
    login(client,*DEV);assert post(client,'/api/requests/1/reject',{'revision':2,'reason':'Outside this launch budget.'}).status_code==200
    assert change(client,1)['status']=='rejected'


@pytest.mark.parametrize('revision',[None,0,-1,True,'1',2])
def test_revision_guards(client,revision):
    login(client,*DESIGNER)
    expected=409 if revision==2 and not isinstance(revision,bool) else 400
    assert post(client,'/api/requests/1/approve',{'revision':revision}).status_code==expected


@pytest.mark.parametrize('amount,days',[('-28000.01',0),('0',-14)])
def test_acceptance_cannot_make_budget_negative_or_schedule_zero(client,amount,days):
    login(client,*DEV);created=post(client,'/api/requests',{**BASE,'amount':amount,'days':days}).json['change']
    assert post(client,f"/api/requests/{created['id']}/submit",{'revision':1}).status_code==200
    login(client,*DESIGNER);assert post(client,f"/api/requests/{created['id']}/approve",{'revision':2}).status_code==409
    assert change(client,created['id'])['status']=='awaiting_designer'


def test_atomic_concurrent_acceptance_has_one_event(app):
    actor=user(app,DESIGNER[0]);barrier=Barrier(2,timeout=20)
    def worker(_):
        barrier.wait()
        try:return domain.transition(app.config['DB_PATH'],actor,1,{'revision':1},'approve')
        except Problem as error:return error.status
    results=run_race(barrier,worker,[1,2])
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results
    db=connect(app.config['DB_PATH']);assert db.execute("SELECT COUNT(*) FROM events WHERE request_id=1 AND kind='approve'").fetchone()[0]==1;db.close()


def test_simultaneous_discounts_cannot_overdraw_agreed_plan(app):
    client_actor=user(app,DEV[0]);designer=user(app,DESIGNER[0]);ids=[]
    for title in ['Discount one','Discount two']:
        r=domain.create(app.config['DB_PATH'],client_actor,{**BASE,'title':title,'amount':'-20000','days':0});ids.append(r['id'])
        domain.transition(app.config['DB_PATH'],client_actor,r['id'],{'revision':1},'submit')
    barrier=Barrier(2,timeout=20)
    def worker(request_id):
        barrier.wait()
        try:return domain.transition(app.config['DB_PATH'],designer,request_id,{'revision':2},'approve')
        except Problem as error:return error.status
    results=run_race(barrier,worker,ids)
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results


def test_audit_failure_rolls_back_version_and_approvals(app,monkeypatch):
    actor=user(app,DESIGNER[0])
    def fail(*_):raise RuntimeError('Simulated audit storage failure')
    monkeypatch.setattr(domain,'event',fail)
    with pytest.raises(RuntimeError):domain.revise(app.config['DB_PATH'],actor,1,{**BASE,'revision':1})
    db=connect(app.config['DB_PATH']);row=db.execute('SELECT * FROM requests WHERE id=1').fetchone()
    assert row['current_version']==1 and row['revision']==1 and row['client_approved_version']==1
    assert db.execute('SELECT COUNT(*) FROM versions WHERE request_id=1').fetchone()[0]==1;db.close()


def test_attachments_are_bounded_versioned_and_ownership_protected(client):
    login(client,*DEV);r=upload(client,2,1,'../../brief.txt');assert r.status_code==201
    row=r.json;assert row['change']['revision']==2
    assert row['change']['attachments'][0]['filename']=='brief.txt'
    attachment=row['attachment_id'];download=client.get(f'/api/attachments/{attachment}')
    assert download.status_code==200 and download.data==b'Packaging sizes: 250g and 500g'
    assert 'attachment;' in download.headers['Content-Disposition'] and download.headers['X-Content-Type-Options']=='nosniff'
    login(client,*NISHA);assert client.get(f'/api/attachments/{attachment}').status_code==404
    login(client,*DESIGNER);assert client.get(f'/api/attachments/{attachment}').status_code==200
    assert upload(client,2,2).status_code==403


def test_attachments_freeze_after_submit_and_previous_version_remains_scoped(client):
    login(client,*DEV);assert upload(client,2,1).status_code==201
    assert post(client,'/api/requests/2/revise',{**BASE,'revision':2}).status_code==200
    row=change(client,2);assert row['current_version']==2 and row['attachments'][0]['version']==1
    assert post(client,'/api/requests/2/submit',{'revision':3}).status_code==200
    assert upload(client,2,4).status_code==409


@pytest.mark.parametrize('filename,content,status',[
 ('brief.html',b'<script>alert(1)</script>',400),('brief.exe',b'Binary',400),
 ('brief.txt',b'\xff\x00',400),('brief.txt',b'with\x00null',400),
 ('brief.pdf',b'not a pdf',400),('brief.pdf',b'%PDF-1.7\nDemo fixture',201),
 ('brief.txt',b'',413),('brief.txt',b'x'*(256*1024+1),413),
 ('x'*101+'.txt',b'Text',400),('',b'Text',400),
],ids=['html-rejected','executable-rejected','invalid-utf8','null-byte-rejected',
       'invalid-pdf','pdf-signature-accepted','empty-file','over-size-limit',
       'long-filename','missing-filename'])
def test_attachment_validation(client,filename,content,status):
    login(client,*DEV);assert upload(client,2,1,filename,content).status_code==status


def test_attachment_missing_stale_revision_and_bad_revision(client):
    login(client,*DEV)
    token=client.get('/api/session').json['csrf_token']
    assert client.post('/api/requests/2/attachments',data={'revision':'1'},headers={'X-CSRF-Token':token}).status_code==400
    assert upload(client,2,99).status_code==409
    assert upload(client,2,'invalid').status_code==400
    assert client.get('/api/attachments/999').status_code==404


def test_payload_unknown_action_and_invalid_reason(client):
    login(client,*DESIGNER)
    assert post(client,'/api/requests/1/unknown',{'revision':1}).status_code==404
    assert post(client,'/api/requests/1/reject',{'revision':1,'reason':'x'}).status_code==400
    token=client.get('/api/session').json['csrf_token']
    assert client.post('/api/requests',data='[1]',content_type='application/json',headers={'X-CSRF-Token':token}).status_code==400
    assert client.get('/api/requests/999').status_code==404


def test_audit_and_attachment_rows_cannot_be_rewritten(client,app):
    login(client,*DEV);assert upload(client,2,1).status_code==201
    db=connect(app.config['DB_PATH'])
    for statement in ['UPDATE events SET detail=\'Tampered\' WHERE request_id=2','DELETE FROM events WHERE request_id=2','UPDATE attachments SET filename=\'new.txt\' WHERE request_id=2','DELETE FROM attachments WHERE request_id=2']:
        with pytest.raises(sqlite3.IntegrityError):db.execute(statement)
    db.close()


def test_attachment_and_submission_race_cannot_change_approved_version(app):
    barrier=Barrier(2,timeout=20)
    def worker(kind):
        client=app.test_client();login(client,*DEV);barrier.wait()
        if kind=='attach':return upload(client,2,1).status_code
        return post(client,'/api/requests/2/submit',{'revision':1}).status_code
    results=run_race(barrier,worker,['attach','submit'])
    assert 409 in results and sum(r in (200,201) for r in results)==1
    db=connect(app.config['DB_PATH']);row=db.execute('SELECT * FROM requests WHERE id=2').fetchone()
    attachments=db.execute('SELECT COUNT(*) FROM attachments WHERE request_id=2').fetchone()[0]
    assert (row['status']=='draft' and attachments==1) or (row['status']=='awaiting_designer' and attachments==0)
    db.close()


def test_oversized_multipart_is_rejected_before_storage(client):
    login(client,*DEV);assert upload(client,2,1,'large.txt',b'x'*(350*1024)).status_code==413


def test_invalid_session_identity_and_corrupt_secret(client,tmp_path):
    from app import create_app
    with client.session_transaction() as session:session['user_id']=999
    assert client.get('/api/projects').status_code==401
    assert client.get('/api/session').json['user'] is None
    folder=tmp_path/'bad-secret';folder.mkdir();(folder/'.session-secret').write_text('bad')
    with pytest.raises(RuntimeError):create_app({'DATA_DIR':str(folder),'DEMO':False})


@pytest.mark.parametrize('password',[' Client@2026','Client@2026 '])
def test_password_whitespace_is_not_silently_normalized(client,password):
    assert post(client,'/api/login',{'email':DEV[0],'password':password}).status_code==401


def test_race_setup_failure_aborts_waiting_peer_and_surfaces_original_error():
    import time
    barrier=Barrier(2,timeout=20)
    def worker(value):
        if value=='failed-login':raise AssertionError('Injected worker sign-in failure')
        barrier.wait()
        return 'finished'
    started=time.monotonic()
    with pytest.raises(AssertionError,match='Injected worker sign-in failure'):
        run_race(barrier,worker,['waiting-peer','failed-login'])
    assert time.monotonic()-started<2


def test_test_runner_terminates_a_stalled_subprocess():
    import sys
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'tools/run_tests.py'
    spec=importlib.util.spec_from_file_location('bounded_runner',path)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    assert runner.run([sys.executable,'-c','import time; time.sleep(60)'],timeout=0.2)==124
