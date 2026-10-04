"""Immutable change proposals and two-party consent with exact INR arithmetic."""
import re
from decimal import Decimal
from core import (Problem, USER_SCHEMA, add_users, connect, now, positive_id,
                  require_revision, require_role, text, transaction)

SCHEMA='''
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, scope TEXT NOT NULL,
 client_id INTEGER NOT NULL REFERENCES users(id), designer_id INTEGER NOT NULL REFERENCES users(id),
 agreed_paise INTEGER NOT NULL CHECK(agreed_paise>=0), agreed_days INTEGER NOT NULL CHECK(agreed_days>0)
);
CREATE TABLE IF NOT EXISTS requests (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
 status TEXT NOT NULL CHECK(status IN ('draft','awaiting_designer','awaiting_client','accepted','rejected')) DEFAULT 'draft',
 revision INTEGER NOT NULL DEFAULT 1, current_version INTEGER NOT NULL DEFAULT 1,
 accepted_version INTEGER, client_approved_version INTEGER, designer_approved_version INTEGER,
 created_at INTEGER NOT NULL,
 CHECK(status!='accepted' OR (accepted_version=current_version AND client_approved_version=current_version AND designer_approved_version=current_version))
);
CREATE TABLE IF NOT EXISTS versions (
 request_id INTEGER NOT NULL REFERENCES requests(id), version INTEGER NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL, delta_paise INTEGER NOT NULL,
 delta_days INTEGER NOT NULL, author_id INTEGER NOT NULL REFERENCES users(id), created_at INTEGER NOT NULL,
 PRIMARY KEY(request_id,version)
);
CREATE TRIGGER IF NOT EXISTS immutable_versions_update BEFORE UPDATE ON versions BEGIN SELECT RAISE(ABORT,'Versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_versions_delete BEFORE DELETE ON versions BEGIN SELECT RAISE(ABORT,'Versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_project_update BEFORE UPDATE ON projects BEGIN SELECT RAISE(ABORT,'Agreed scopes are immutable'); END;
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id),
 actor_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
 id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL, version INTEGER NOT NULL,
 uploader_id INTEGER NOT NULL REFERENCES users(id), filename TEXT NOT NULL,
 mime TEXT NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, created_at INTEGER NOT NULL,
 FOREIGN KEY(request_id,version) REFERENCES versions(request_id,version)
);

CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_attachments_update BEFORE UPDATE ON attachments BEGIN SELECT RAISE(ABORT,'Attachments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_attachments_delete BEFORE DELETE ON attachments BEGIN SELECT RAISE(ABORT,'Attachments are immutable'); END;
'''
DEMO_USERS=[
 ('Ishita Shah','designer@daayra.demo','designer','Studio','Studio@2026'),
 ('Dev Patel','dev@daayra.demo','client','Meera Foods','Client@2026'),
 ('Nisha Rao','nisha@daayra.demo','client','Tara Books','Client@2026'),
]


def initialize(path,demo):
    db=connect(path);db.execute('PRAGMA journal_mode=WAL');db.executescript(USER_SCHEMA+SCHEMA);db.close()
    if demo:
        with transaction(path) as db:
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                add_users(db,DEMO_USERS)
                db.execute('INSERT INTO projects VALUES(1,?,?,?,?,?,?)',('Meera Foods · Identity kit','One primary logo, two colour variants, a type pairing and a four-page brand guide. Two feedback rounds. Packaging and social templates are outside this scope.',2,1,2800000,14))
                db.execute('INSERT INTO projects VALUES(2,?,?,?,?,?,?)',('Tara Books · Landing page','One responsive landing page with catalogue highlights, store location and an enquiry form. Copy supplied by the client. No ecommerce or account system.',3,1,3600000,18))
                draft(db,1,2,{'title':'Add festival packaging labels','description':'Three Diwali label variations for 250 g, 500 g and 1 kg packs, using the approved identity. Print-ready PDF files.','amount':'4500.00','days':3},'awaiting_designer')
                draft(db,1,2,{'title':'Instagram launch cards','description':'Six editable launch cards using the new colour and typography system.','amount':'3200.00','days':2})
                draft(db,2,3,{'title':'Add a reading events section','description':'A small events list for the landing page with date, author and a booking link.','amount':'2400.00','days':2},'awaiting_designer')


def money(value):
    if not isinstance(value,str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?',value):
        raise Problem('Amount must be an INR decimal with at most two decimal places.')
    paise=int(Decimal(value)*100)
    if not -100000000<=paise<=100000000:raise Problem('Price adjustment must be between -₹10,00,000 and ₹10,00,000.')
    return paise


def proposal(data):
    days=data.get('days')
    if isinstance(days,bool) or not isinstance(days,int) or not -90<=days<=90:raise Problem('Schedule adjustment must be an integer from -90 to 90 days.')
    return text(data.get('title'),'Title',80),text(data.get('description'),'Scope details',1800,10),money(data.get('amount')),days


def access_project(db,user,project_id):
    p=db.execute('SELECT * FROM projects WHERE id=?',(positive_id(project_id),)).fetchone()
    if p is None or user['id'] not in (p['client_id'],p['designer_id']):raise Problem('Project not found.',404)
    return p


def visible(db,user,request_id):
    row=db.execute('SELECT * FROM requests WHERE id=?',(positive_id(request_id),)).fetchone()
    if row is None:raise Problem('Change request not found.',404)
    access_project(db,user,row['project_id'])
    return row


def event(db,request_id,actor,kind,detail):
    db.execute('INSERT INTO events(request_id,actor_id,kind,detail,created_at) VALUES(?,?,?,?,?)',(request_id,actor,kind,detail,now()))


def draft(db,project_id,actor,data,status='draft'):
    values=proposal(data)
    cursor=db.execute('INSERT INTO requests(project_id,status,created_at,client_approved_version) VALUES(?,?,?,?)',
                      (project_id,status,now(),1 if status=='awaiting_designer' else None))
    db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)',(cursor.lastrowid,1,*values,actor,now()))
    event(db,cursor.lastrowid,actor,'submitted' if status=='awaiting_designer' else 'draft_created','Version 1 created; original scope retained.')
    return cursor.lastrowid


def serialize(db,row):
    result=dict(row)
    result['versions']=[dict(v) for v in db.execute('SELECT v.*,u.name AS author FROM versions v JOIN users u ON u.id=v.author_id WHERE request_id=? ORDER BY version',(row['id'],))]
    result['current']=result['versions'][-1]
    result['attachments']=[dict(v) for v in db.execute('SELECT id,version,filename,mime,sha256,created_at FROM attachments WHERE request_id=? ORDER BY id',(row['id'],))]
    return result


def list_projects(db,user):
    result=[]
    for p in db.execute('SELECT p.*,c.name AS client_name,d.name AS designer_name FROM projects p JOIN users c ON c.id=p.client_id JOIN users d ON d.id=p.designer_id WHERE p.client_id=? OR p.designer_id=? ORDER BY p.id',(user['id'],user['id'])):
        row=dict(p)
        accepted=db.execute("SELECT COALESCE(SUM(v.delta_paise),0) AS price,COALESCE(SUM(v.delta_days),0) AS days FROM requests r JOIN versions v ON v.request_id=r.id AND v.version=r.accepted_version WHERE r.project_id=? AND r.status='accepted'",(p['id'],)).fetchone()
        row['current_paise']=p['agreed_paise']+accepted['price'];row['current_days']=p['agreed_days']+accepted['days']
        row['requests']=[serialize(db,r) for r in db.execute('SELECT * FROM requests WHERE project_id=? ORDER BY id DESC',(p['id'],))]
        result.append(row)
    return result


def create(path,user,data):
    require_role(user,'client')
    with transaction(path) as db:
        p=access_project(db,user,data.get('project_id'))
        if p['client_id']!=user['id']:raise Problem('Only the project client can create requests.',403)
        request_id=draft(db,p['id'],user['id'],data)
        return serialize(db,visible(db,user,request_id))


def revise(path,user,request_id,data):
    values=proposal(data)
    with transaction(path) as db:
        row=visible(db,user,request_id);require_revision(row,data.get('revision'))
        client_edit=user['role']=='client' and row['status']=='draft'
        designer_edit=user['role']=='designer' and row['status']=='awaiting_designer'
        if not (client_edit or designer_edit):raise Problem('This account cannot revise the current request state.',409)
        version=row['current_version']+1
        db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)',(request_id,version,*values,user['id'],now()))
        db.execute('UPDATE requests SET current_version=?,revision=revision+1,status=?,client_approved_version=NULL,designer_approved_version=? WHERE id=?',
                   (version,'draft' if client_edit else 'awaiting_client',None if client_edit else version,request_id))
        event(db,request_id,user['id'],'revised',f'Immutable version {version} created; '+('ready for client review.' if designer_edit else 'draft remains editable.'))
        return serialize(db,visible(db,user,request_id))


def approve_budget(db,row):
    p=db.execute('SELECT * FROM projects WHERE id=?',(row['project_id'],)).fetchone()
    sums=db.execute("SELECT COALESCE(SUM(v.delta_paise),0) AS price,COALESCE(SUM(v.delta_days),0) AS days FROM requests r JOIN versions v ON v.request_id=r.id AND v.version=r.accepted_version WHERE r.project_id=? AND r.status='accepted'",(row['project_id'],)).fetchone()
    version=db.execute('SELECT * FROM versions WHERE request_id=? AND version=?',(row['id'],row['current_version'])).fetchone()
    if p['agreed_paise']+sums['price']+version['delta_paise']<0 or p['agreed_days']+sums['days']+version['delta_days']<1:
        raise Problem('Accepted changes cannot make the project price negative or delivery shorter than one day.',409)


def transition(path,user,request_id,data,action):
    with transaction(path) as db:
        row=visible(db,user,request_id);require_revision(row,data.get('revision'))
        v=row['current_version']
        if action=='submit':
            if user['role']!='client' or row['status']!='draft':raise Problem('Only the client can submit a draft.',409)
            db.execute("UPDATE requests SET status='awaiting_designer',client_approved_version=?,revision=revision+1 WHERE id=?",(v,request_id))
            detail=f'Client approved version {v}; awaiting designer.'
        elif action=='approve':
            expected='awaiting_designer' if user['role']=='designer' else 'awaiting_client'
            if row['status']!=expected:raise Problem('This request is not awaiting your approval.',409)
            approve_budget(db,row)
            db.execute("UPDATE requests SET status='accepted',accepted_version=?,client_approved_version=?,designer_approved_version=?,revision=revision+1 WHERE id=?",(v,v,v,request_id))
            detail=f'Both parties approved version {v}; price and schedule adjustment accepted.'
        elif action=='reject':
            expected='awaiting_designer' if user['role']=='designer' else 'awaiting_client'
            if row['status']!=expected:raise Problem('This request is not awaiting your review.',409)
            reason=text(data.get('reason'),'Rejection reason',400,3)
            db.execute("UPDATE requests SET status='rejected',revision=revision+1 WHERE id=?",(request_id,))
            detail=f'Version {v} rejected: {reason}'
        else:raise Problem('Unknown action.',404)
        event(db,request_id,user['id'],action,detail)
        return serialize(db,visible(db,user,request_id))
