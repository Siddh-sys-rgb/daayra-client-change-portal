'use strict';
const $ = (s) => document.querySelector(s);
const state = {user:null, csrf:'', projects:[], projectId:null, filter:'all', editing:null};
const escape = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:2,minimumFractionDigits:0}).format(value/100);
const adjustment = value => `${value > 0 ? '+' : ''}${money(value)}`;
const days = value => `${value > 0 ? '+' : ''}${value} ${Math.abs(value) === 1 ? 'day' : 'days'}`;
const date = at => new Date(at*1000).toLocaleString('en-IN',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
const statusLabels = {draft:'Client draft',awaiting_designer:'Designer review',awaiting_client:'Client review',accepted:'Both parties agreed',rejected:'Not agreed'};
async function api(path,options={}) {
 const isForm=options.body instanceof FormData;
 const response=await fetch(path,{...options,headers:{'X-CSRF-Token':state.csrf,...(options.body && !isForm ? {'Content-Type':'application/json'} : {}),...options.headers}});
 const data=await response.json();if(!response.ok)throw new Error(data.error||'Please try again.');return data;
}
function announce(message,error=false){$('#notice').textContent=message;$('#notice').hidden=!message;$('#notice').classList.toggle('error',error);}
function currentProject(){return state.projects.find(p=>p.id===state.projectId)||state.projects[0];}
async function refresh(){state.projects=(await api('/api/projects')).projects;if(!state.projects.some(p=>p.id===state.projectId))state.projectId=state.projects[0]?.id;render();}
function render(){
 const signedIn=Boolean(state.user);$('#login-panel').hidden=signedIn;$('#workspace').hidden=!signedIn;$('#account').hidden=!signedIn;$('#project-navigation').hidden=!signedIn;
 if(!signedIn){$('#breadcrumb-title').textContent='WELCOME';return;}
 $('#account-name').textContent=`${state.user.name} · ${state.user.role}`;
 const p=currentProject();if(!p){$('#workspace').innerHTML='<div class="empty"><strong>No projects yet.</strong>This account has no assigned project.</div>';return;}
 const nav=state.projects.map(project=>`<button type="button" data-project="${project.id}" class="${project.id===p.id?'active':''}">${escape(project.title)}<span>${escape(project.client_name)}</span></button>`).join('');
 $('#project-list').innerHTML=nav;$('#mobile-project-list').innerHTML=nav;
 $('#breadcrumb-title').textContent=p.title.toUpperCase();$('#project-client').textContent=p.client_name;$('#project-title').textContent=p.title;$('#project-scope').textContent=p.scope;
 $('#new-request').hidden=state.user.role!=='client';$('#base-budget').textContent=money(p.agreed_paise);$('#base-days').textContent=`${p.agreed_days} days · initial delivery plan`;
 $('#current-budget').textContent=money(p.current_paise);$('#current-days').textContent=`${p.current_days} days · includes accepted changes`;
 $('#pending-count').textContent=String(p.requests.filter(r=>r.status==='awaiting_designer'||r.status==='awaiting_client').length).padStart(2,'0');
 const rows=p.requests.filter(r=>state.filter==='all'||(state.filter==='open'&&!['accepted','rejected'].includes(r.status))||r.status===state.filter);
 $('#request-list').innerHTML=rows.map(r=>`<article class="request-row"><span class="request-number">${String(r.id).padStart(2,'0')}</span><div class="request-content"><h3>${escape(r.current.title)}</h3><p>${escape(r.current.description)}</p><small>VERSION ${r.current_version} / ${escape(r.current.author)} / ${date(r.created_at)}</small></div><div class="adjustment">${adjustment(r.current.delta_paise)}<small>${days(r.current.delta_days)} to delivery</small></div><div class="request-status"><span class="status ${r.status}">${statusLabels[r.status]}</span><button type="button" data-request="${r.id}">Read the brief ↗</button></div></article>`).join('')||'<div class="empty"><strong>A little breathing room.</strong>No change requests match this view.</div>';
 document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===state.filter));
}
function editForm(change=null){
 state.editing=change;
 $('#form-eyebrow').textContent=change?`CREATE VERSION ${change.current_version+1}`:'WRITE A CHANGE BRIEF';
 $('#form-title').textContent=change?'Make the next version.':'What should change?';
 $('#form-explanation').textContent=change?(state.user.role==='designer'?'Your revision signs this new version and sends it to the client for fresh approval.':'Your old version stays in the journal. This new version remains a draft.'):'Start a draft. The original project scope stays intact until both parties agree to this change.';
 $('#change-title').value=change?.current.title||'';$('#change-description').value=change?.current.description||'';$('#change-amount').value=((change?.current.delta_paise||0)/100).toFixed(2);$('#change-days').value=change?.current.delta_days||0;
 $('#save-brief').innerHTML=state.user.role==='designer'?'Propose revised terms <span>↗</span>':'Save draft <span>↗</span>';
 $('#request-error').textContent='';$('#request-dialog').showModal();
}
async function openRequest(id){
 const {change:r}=await api(`/api/requests/${id}`);const {events}=await api(`/api/requests/${id}/events`);
 const clientDraft=state.user.role==='client'&&r.status==='draft';const canRevise=clientDraft||(state.user.role==='designer'&&r.status==='awaiting_designer');
 const canApprove=(state.user.role==='designer'&&r.status==='awaiting_designer')||(state.user.role==='client'&&r.status==='awaiting_client');
 $('#request-detail').innerHTML=`<div class="dialog-title"><div><p class="eyebrow">CHANGE / ${String(r.id).padStart(2,'0')} · REVISION ${r.revision}</p><h2>${escape(r.current.title)}</h2></div><button type="button" class="close" data-close="detail-dialog" aria-label="Close change details">×</button></div><div class="version-heading"><span>${r.versions.length} IMMUTABLE VERSION${r.versions.length===1?'':'S'}</span><span class="status ${r.status}">${statusLabels[r.status]}</span></div>${r.status==='accepted'?`<p class="approval-note">Version ${r.accepted_version} is pinned. Both parties agreed to exactly these terms; the original brief is retained.</p>`:''}${r.versions.map(v=>`<article class="version-sheet"><div class="version-top"><span>VERSION ${v.version}${v.version===r.accepted_version?' / ACCEPTED':v.version===r.current_version?' / CURRENT':''}</span><span>${escape(v.author)} · ${date(v.created_at)}</span></div><h3>${escape(v.title)}</h3><p>${escape(v.description)}</p><div class="version-costs"><span>PRICE / ${adjustment(v.delta_paise)}</span><span>DELIVERY / ${days(v.delta_days)}</span></div></article>`).join('')}<div class="detail-actions">${clientDraft?'<button class="primary" type="button" data-action="submit">Approve & send to designer ↗</button>':''}${canApprove?'<button class="primary" type="button" data-action="approve">Agree to this version ↗</button>':''}${canRevise?'<button type="button" id="revise-brief">Write a new version</button>':''}${canApprove?'<button type="button" class="reject" id="show-reject">Decline this change</button>':''}</div><form id="reject-form" class="reject-form" hidden><label>Why is this change not agreed?<textarea id="reject-reason" minlength="3" maxlength="400" required></textarea></label><button class="primary" type="submit">Confirm rejection ↗</button></form><p id="detail-error" class="error" role="alert"></p><p class="section-label">ATTACHMENTS / VERSION-SCOPED</p><ul class="attachment-list">${r.attachments.map(a=>`<li><a href="/api/attachments/${a.id}" download>${escape(a.filename)}</a><span>VERSION ${a.version} · ${escape(a.sha256.slice(0,12))}…</span></li>`).join('')||'<li>No attachments in this change.</li>'}</ul>${clientDraft?'<form id="upload-form"><label>Attach to the current draft version<input id="attachment-file" type="file" accept=".txt,.pdf" required></label><p class="field-note">UTF-8 text or a PDF, up to 256 KiB. Downloads require project access.</p><button class="primary" type="submit">Attach to this version ↗</button></form>':''}<p class="section-label">03 / THE DECISION TRAIL</p><ol class="history">${events.map(e=>`<li><strong>${escape(e.actor)} · ${escape(e.kind.replaceAll('_',' '))}</strong><small>${date(e.created_at)}</small><p>${escape(e.detail)}</p></li>`).join('')}</ol>`;
 $('#detail-dialog').showModal();
 async function act(action,extra={}){
  try{await api(`/api/requests/${id}/${action}`,{method:'POST',body:JSON.stringify({revision:r.revision,...extra})});$('#detail-dialog').close();await refresh();announce(action==='approve'?'Change agreed. The accepted version is pinned and the project plan is updated.':action==='submit'?'Client approved this version. The designer now reviews the same terms.':'Change declined. The original agreement remains intact.');}
  catch(error){$('#detail-error').textContent=error.message;}
 }
 document.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;await act(b.dataset.action);b.disabled=false;}));
 $('#revise-brief')?.addEventListener('click',()=>{$('#detail-dialog').close();editForm(r);});
 $('#show-reject')?.addEventListener('click',()=>{$('#reject-form').hidden=!$('#reject-form').hidden;$('#reject-reason').focus();});
 $('#reject-form')?.addEventListener('submit',async e=>{e.preventDefault();e.submitter.disabled=true;await act('reject',{reason:$('#reject-reason').value});e.submitter.disabled=false;});
 $('#upload-form')?.addEventListener('submit',async e=>{
  e.preventDefault();e.submitter.disabled=true;const body=new FormData();body.append('file',$('#attachment-file').files[0]);body.append('revision',String(r.revision));
  try{await api(`/api/requests/${id}/attachments`,{method:'POST',body});$('#detail-dialog').close();await refresh();await openRequest(id);}
  catch(error){$('#detail-error').textContent=error.message;e.submitter.disabled=false;}
 });
}
$('#login-form').addEventListener('submit',async e=>{e.preventDefault();e.submitter.disabled=true;$('#login-error').textContent='';try{const data=await api('/api/login',{method:'POST',body:JSON.stringify({email:$('#login-email').value,password:$('#login-password').value})});state.user=data.user;state.csrf=data.csrf_token;await refresh();announce('Signed in. These are fictional briefs; no contracts or payments are issued.');}catch(error){$('#login-error').textContent=error.message;}finally{e.submitter.disabled=false;}});
$('#logout').addEventListener('click',async()=>{try{const data=await api('/api/logout',{method:'POST',body:'{}'});state.user=null;state.csrf=data.csrf_token;state.projects=[];state.projectId=null;announce('');render();}catch(error){announce(error.message,true);}});
document.querySelectorAll('[data-demo]').forEach(b=>b.addEventListener('click',()=>{$('#login-email').value=`${b.dataset.demo}@daayra.demo`;$('#login-password').value=b.dataset.demo==='designer'?'Studio@2026':'Client@2026';$('#login-error').textContent='';}));
$('#new-request').addEventListener('click',()=>editForm());
$('#request-form').addEventListener('submit',async e=>{
 e.preventDefault();e.submitter.disabled=true;const data={title:$('#change-title').value,description:$('#change-description').value,amount:$('#change-amount').value,days:Number($('#change-days').value)};
 let path='/api/requests';if(state.editing){path+=`/${state.editing.id}/revise`;data.revision=state.editing.revision;}else data.project_id=currentProject().id;
 try{await api(path,{method:'POST',body:JSON.stringify(data)});$('#request-dialog').close();await refresh();announce(state.editing?'A new immutable version was created. Earlier terms remain in the journal.':'Draft created. Review the brief, then approve and send it to the designer.');}catch(error){$('#request-error').textContent=error.message;}finally{e.submitter.disabled=false;}
});
document.addEventListener('click',async e=>{const close=e.target.closest('[data-close]');if(close)$(`#${close.dataset.close}`).close();const project=e.target.closest('[data-project]');if(project){state.projectId=Number(project.dataset.project);state.filter='all';render();}const request=e.target.closest('[data-request]');if(request){try{await openRequest(Number(request.dataset.request));}catch(error){announce(error.message,true);}}});
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{state.filter=b.dataset.filter;render();}));
(async()=>{try{const data=await api('/api/session');state.csrf=data.csrf_token;state.user=data.user;if(state.user)await refresh();else render();}catch(error){announce(error.message,true);}})();
