"""Flask factory and source-independent command-line launcher."""
import argparse
import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from flask import Flask, jsonify, render_template, request, session, send_file
from core import Problem, authenticate, connect, now, secret_file, transaction, user_for
import domain

APP_NAME = 'Daayra — Client Change Portal'
DEFAULT_PORT = 8113


def create_app(config=None):
    app=Flask(__name__)
    app.config.update(DATA_DIR=str(Path(__file__).resolve().parent/'instance'), DEMO=True,
                      MAX_CONTENT_LENGTH=300*1024, SESSION_COOKIE_NAME='daayra_session',
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      PERMANENT_SESSION_LIFETIME=28800)
    if config:app.config.update(config)
    directory=Path(app.config['DATA_DIR']);directory.mkdir(parents=True,exist_ok=True)
    app.secret_key=secret_file(directory)
    db_path=directory/'app.sqlite3'
    app.config['DB_PATH']=str(db_path)
    domain.initialize(db_path,app.config['DEMO'])
    with transaction(db_path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS login_attempts(identity TEXT PRIMARY KEY,failures INTEGER NOT NULL,first_at INTEGER NOT NULL)')

    @app.errorhandler(Problem)
    def invalid(error):return jsonify(error=error.message),error.status

    @app.errorhandler(413)
    def large(_):return jsonify(error='Request too large. Attachments are limited to 256 KiB.'),413

    @app.errorhandler(404)
    def missing(_):return jsonify(error='Page or API route not found.'),404

    @app.errorhandler(sqlite3.OperationalError)
    def unavailable(_):return jsonify(error='Storage is temporarily unavailable. Retry shortly.'),503

    @app.errorhandler(sqlite3.IntegrityError)
    def constraint(_):return jsonify(error='The operation conflicts with the current record state.'),409

    @app.before_request
    def protect():
        if request.method in ('POST','PUT','PATCH','DELETE'):
            supplied=request.headers.get('X-CSRF-Token','')
            expected=session.get('csrf','')
            if not expected or not hmac.compare_digest(supplied,expected):
                raise Problem('Request token is missing or expired. Refresh and try again.',403)
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
                raise Problem('Cross-origin requests are not allowed.',403)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if request.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    def payload():
        data=request.get_json(silent=True)
        if not isinstance(data,dict):raise Problem('Send a JSON object with application/json content type.')
        return data

    def identity():
        db=connect(db_path)
        try:return dict(user_for(db))
        finally:db.close()

    @app.get('/')
    def home():return render_template('index.html',demo=app.config['DEMO'])

    @app.get('/api/health')
    def health():
        db=connect(db_path)
        try:db.execute('SELECT 1').fetchone()
        finally:db.close()
        return jsonify(status='ok',app=APP_NAME,demo=app.config['DEMO'])

    @app.get('/api/session')
    def session_info():
        if not session.get('csrf'):session['csrf']=secrets.token_urlsafe(32)
        user=None
        if session.get('user_id'):
            try:user=identity()
            except Problem:session.pop('user_id',None)
        return jsonify(user=user,csrf_token=session['csrf'],demo=app.config['DEMO'])

    @app.post('/api/login')
    def login():
        data=payload()
        email=data.get('email');password=data.get('password')
        if not isinstance(email,str) or not isinstance(password,str):raise Problem('Email and password are required.')
        key=hashlib.sha256(email.strip().lower().encode()).hexdigest()
        denied=None;user=None
        with transaction(db_path) as db:
            db.execute('DELETE FROM login_attempts WHERE first_at<?',(now()-900,))
            prior=db.execute('SELECT * FROM login_attempts WHERE identity=?',(key,)).fetchone()
            if prior and prior['failures']>=8:raise Problem('Too many failed sign-ins. Try again in 15 minutes.',429)
            try:user=authenticate(db,email,password)
            except Problem as error:
                db.execute('INSERT INTO login_attempts(identity,failures,first_at) VALUES(?,1,?) ON CONFLICT(identity) DO UPDATE SET failures=failures+1',(key,now()))
                denied=error
            if user:db.execute('DELETE FROM login_attempts WHERE identity=?',(key,))
        if denied:raise denied
        session.clear();session['user_id']=user['id'];session['csrf']=secrets.token_urlsafe(32);session.permanent=True
        return jsonify(user=user,csrf_token=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear();session['csrf']=secrets.token_urlsafe(32)
        return jsonify(csrf_token=session['csrf'])

    @app.get('/api/projects')
    def projects():
        user=identity();db=connect(db_path)
        try:rows=domain.list_projects(db,user)
        finally:db.close()
        return jsonify(projects=rows)

    @app.post('/api/requests')
    def create_request():return jsonify(change=domain.create(db_path,identity(),payload())),201

    @app.get('/api/requests/<int:request_id>')
    def get_request(request_id):
        user=identity();db=connect(db_path)
        try:result=domain.serialize(db,domain.visible(db,user,request_id))
        finally:db.close()
        return jsonify(change=result)

    @app.post('/api/requests/<int:request_id>/revise')
    def revise(request_id):return jsonify(change=domain.revise(db_path,identity(),request_id,payload()))

    @app.post('/api/requests/<int:request_id>/<action>')
    def transition(request_id,action):return jsonify(change=domain.transition(db_path,identity(),request_id,payload(),action))

    @app.get('/api/requests/<int:request_id>/events')
    def history(request_id):
        user=identity();db=connect(db_path)
        try:
            domain.visible(db,user,request_id)
            rows=[dict(r) for r in db.execute('SELECT e.*,u.name AS actor FROM events e JOIN users u ON u.id=e.actor_id WHERE request_id=? ORDER BY e.id',(request_id,))]
        finally:db.close()
        return jsonify(events=rows)

    @app.post('/api/requests/<int:request_id>/attachments')
    def upload(request_id):
        import hashlib
        from werkzeug.utils import secure_filename
        user=identity()
        if user['role']!='client':raise Problem('Only the client can attach files to a draft.',403)
        attachment=request.files.get('file')
        if attachment is None:raise Problem('Choose one PDF or text attachment.')
        filename=secure_filename(attachment.filename or '')
        if not filename or len(filename)>100:raise Problem('Attachment filename is invalid.')
        content=attachment.stream.read(256*1024+1)
        if not content or len(content)>256*1024:raise Problem('Attachment must contain 1 byte–256 KiB.',413)
        suffix=Path(filename).suffix.lower()
        if suffix=='.txt':
            try:decoded=content.decode('utf-8')
            except UnicodeDecodeError:raise Problem('Text attachments must contain UTF-8 text.')
            if '\x00' in decoded:raise Problem('Binary content is not a text attachment.')
            mime='text/plain'
        elif suffix=='.pdf' and content.startswith(b'%PDF-'):
            mime='application/pdf'
        else:raise Problem('Only UTF-8 .txt or a PDF-signature .pdf file is allowed.')
        try:expected=int(request.form.get('revision',''))
        except ValueError:raise Problem('Revision must be a positive integer.')
        from core import require_revision
        with transaction(db_path) as db:
            row=domain.visible(db,user,request_id);require_revision(row,expected)
            if row['status']!='draft':raise Problem('Attachments can only be added to client drafts.',409)
            cursor=db.execute('INSERT INTO attachments(request_id,version,uploader_id,filename,mime,content,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)',
                              (request_id,row['current_version'],user['id'],filename,mime,content,hashlib.sha256(content).hexdigest(),now()))
            db.execute('UPDATE requests SET revision=revision+1 WHERE id=?',(request_id,))
            domain.event(db,request_id,user['id'],'attached',f'Attachment added to version {row["current_version"]}: {filename}')
            return jsonify(attachment_id=cursor.lastrowid,change=domain.serialize(db,domain.visible(db,user,request_id))),201

    @app.get('/api/attachments/<int:attachment_id>')
    def download(attachment_id):
        import io
        user=identity();db=connect(db_path)
        try:
            row=db.execute('SELECT * FROM attachments WHERE id=?',(attachment_id,)).fetchone()
            if row is None:raise Problem('Attachment not found.',404)
            domain.visible(db,user,row['request_id'])
            content,filename,mime=row['content'],row['filename'],row['mime']
        finally:db.close()
        return send_file(io.BytesIO(content),mimetype=mime,as_attachment=True,download_name=filename,max_age=0)

    return app


def main():
    parser=argparse.ArgumentParser(description=APP_NAME+' local demo')
    parser.add_argument('--port',type=int,default=DEFAULT_PORT)
    parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parent/'instance'))
    parser.add_argument('--no-demo',action='store_true',help='Create an empty database; do not seed fictional accounts.')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Port must be from 1 to 65535.')
    create_app({'DATA_DIR':args.data_dir,'DEMO':not args.no_demo}).run(host='127.0.0.1',port=args.port,debug=False)


if __name__=='__main__':main()
