/* Scoutly app shell: shared by dashboard.html and campaigns.html.
   Fills in the signed-in user, runs the mobile sidebar, toasts, "coming soon" links and logout.
   Early-access demo: everything is kept in this tab's sessionStorage; nothing is sent anywhere. */
(function(){
  var $=function(i){return document.getElementById(i)};
  function read(k){try{return sessionStorage.getItem(k)}catch(_){return null}}
  function write(k,v){try{sessionStorage.setItem(k,v)}catch(_){}}

  var user={name:'Farsana N',email:''};
  try{var s=JSON.parse(read('scoutly.user')||'null');if(s)user=s}catch(_){}
  if($('whoName'))$('whoName').textContent=user.name;
  if($('whoMail'))$('whoMail').textContent=user.email;
  if($('av'))$('av').textContent=user.name.split(/\s+/).map(function(w){return w[0]}).join('').slice(0,2).toUpperCase();

  /* toast */
  var toast=$('toast'),tt;
  if(!toast){toast=document.createElement('div');toast.className='toast';toast.id='toast';toast.setAttribute('role','status');toast.setAttribute('aria-live','polite');document.body.appendChild(toast)}
  function say(m){toast.textContent=m;toast.classList.add('on');clearTimeout(tt);tt=setTimeout(function(){toast.classList.remove('on')},3200)}

  /* mobile sidebar */
  var mb=$('menuB');
  function closeNav(){document.body.classList.remove('nav-open');if(mb)mb.setAttribute('aria-expanded','false')}
  if(mb)mb.addEventListener('click',function(){var o=document.body.classList.toggle('nav-open');mb.setAttribute('aria-expanded',o)});
  if($('scrim'))$('scrim').addEventListener('click',closeNav);
  document.addEventListener('keydown',function(e){if(e.key==='Escape')closeNav()});

  document.addEventListener('click',function(e){var a=e.target.closest('[data-soon]');if(!a)return;e.preventDefault();say(a.dataset.soon+' opens when audits go live.');closeNav()});

  /* website status chip in the top bar */
  function showSite(site){
    if(!site)return;document.body.classList.add('has-site');
    if($('statusT'))$('statusT').textContent=site;
    if($('projT'))$('projT').textContent=site;
  }
  showSite(read('scoutly.site'));
  /* pages without the add-website box send the "+ Add a website" button to the dashboard */
  if($('projB')&&!$('scanUrl'))$('projB').addEventListener('click',function(){location.href='dashboard.html'});

  if($('logout'))$('logout').addEventListener('click',function(e){e.preventDefault();
    try{['scoutly.user','scoutly.site','scoutly.campaigns','scoutly.audits'].forEach(function(k){sessionStorage.removeItem(k)})}catch(_){}
    location.href='login.html?mode=login'});

  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
  /* JSON from the local server (python server.py). Errors come back as {error} with a 200. */
  function api(path,opts){
    return fetch(path,opts).then(function(r){return r.text().then(function(txt){var j=null;try{j=JSON.parse(txt)}catch(_){}
      if(!j)throw new Error(r.status===404||r.status===501?'The Scoutly server isn’t running. Start it from the project folder with: python server.py':'The server sent an unexpected response.');
      if(j.error)throw new Error(typeof j.error==='string'?j.error:(j.error.message||'Request failed.'));return j})});
  }
  function csv(rows,name){
    var body=rows.map(function(r){return r.map(function(v){v=v==null?'':String(v);return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}).join(',')}).join('\r\n');
    var a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['﻿'+body],{type:'text/csv'}));a.download=name;document.body.appendChild(a);a.click();
    setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},500);
  }
  /* Keyword Manager lists, kept in this browser (localStorage) so they survive closing the tab. */
  var LK='scoutly.kwlists';
  function lists(){try{return JSON.parse(localStorage.getItem(LK)||'{}')||{}}catch(_){return {}}}
  function saveLists(l){try{localStorage.setItem(LK,JSON.stringify(l))}catch(_){}}
  function addToList(name,kws){var l=lists();name=name||'My keywords';var cur=l[name]||{created:new Date().toISOString(),items:[]};
    var have={};cur.items.forEach(function(k){have[k.kw]=1});var added=0;
    kws.forEach(function(k){if(!have[k.kw]){cur.items.push(k);have[k.kw]=1;added++}});l[name]=cur;saveLists(l);return added}
  function qs(k){return new URLSearchParams(location.search).get(k)||''}

  window.Scoutly={user:user,say:say,closeNav:closeNav,read:read,write:write,showSite:showSite,esc:esc,api:api,csv:csv,lists:lists,saveLists:saveLists,addToList:addToList,qs:qs};
})();
