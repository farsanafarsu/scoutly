/* Scoutly app shell: shared by dashboard.html and campaigns.html.
   Fills in the signed-in user, runs the mobile sidebar, toasts, "coming soon" links and logout.
   Early-access demo: the session is kept in this tab's sessionStorage. Campaigns and backlinks are kept
   in the team database when it's on, otherwise in this browser's localStorage. */
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

  document.addEventListener('click',function(e){var a=e.target.closest('[data-soon]');if(!a)return;e.preventDefault();say(a.dataset.soon+' is coming soon.');closeNav()});

  /* website status chip in the top bar */
  function showSite(site){
    if(!site)return;document.body.classList.add('has-site');
    if($('statusT'))$('statusT').textContent=site;
    if($('projT'))$('projT').textContent=site;
  }
  showSite(read('scoutly.site'));
  /* pages without the add-website box send the "+ Add a website" button to the dashboard */
  if($('projB')&&!$('scanUrl'))$('projB').addEventListener('click',function(){location.href='dashboard.html'});

  /* logout: the sidebar link and the top bar button (campaigns and backlinks are kept: database / localStorage) */
  [].forEach.call(document.querySelectorAll('[data-logout]'),function(el){el.addEventListener('click',function(e){e.preventDefault();
    try{['scoutly.user','scoutly.site','scoutly.audits'].forEach(function(k){sessionStorage.removeItem(k)})}catch(_){}
    location.href='login.html?mode=login'})});

  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
  /* JSON from the local server (python server.py). Errors come back as {error} with a 200. */
  function api(path,opts){
    return fetch(path,opts).then(function(r){return r.text().then(function(txt){var j=null;try{j=JSON.parse(txt)}catch(_){}
      if(!j)throw new Error(r.status===404||r.status===501?'The Scoutly server isn’t running. Start it from the project folder with: python server.py':'The server sent an unexpected response.');
      if(j.error){var e=new Error(typeof j.error==='string'?j.error:(j.error.message||'Request failed.'));e.notConnected=!!j.notConnected;throw e}return j})});
  }
  function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}

  /* Optional DataForSEO data (provider.py on the server; credentials never reach the browser).
     provider() resolves to the status once per page; every app page marks the API badges with it. */
  var prov=null;
  function provider(){
    if(!prov)prov=api('/api/provider/status').catch(function(e){return {ok:false,configured:false,message:e.message}});
    return prov;
  }
  function fmt(n,dp){if(n==null||n==='')return '—';n=+n;if(isNaN(n))return '—';
    if(dp!=null)return n.toLocaleString(undefined,{minimumFractionDigits:dp,maximumFractionDigits:dp});
    return Math.abs(n)>=1e6?(n/1e6).toFixed(n>=1e7?0:1)+'M':Math.abs(n)>=1e4?Math.round(n/1e3)+'K':Math.round(n).toLocaleString()}
  function kdTag(kd){if(kd==null)return '<span style="color:var(--mu)">—</span>';var c=kd<30?'ok':kd<60?'wr':'bad';return '<span class="tag '+c+'" title="Keyword difficulty (0–100)">'+kd+'</span>'}
  function ago(t){var m=Math.round((Date.now()/1000-t)/60);return m<1?'just now':m<60?m+' min ago':Math.round(m/60)+' h ago'}
  /* Paid lookup with a cost check: load(false) answers from the 24-hour cache or says how many calls a run
     makes; then a bar asks before load(true) spends anything. onData gets the result either way. */
  function paid(box,load,onData,what){
    box.hidden=false;box.className='paybar';box.innerHTML='<span class="spin"></span><p>Checking cached '+esc(what)+'…</p>';
    return load(false).then(function(d){
      if(!d.needsRun&&!(d.missing>0)){box.hidden=true;onData(d);return}
      if(d.items||d.partial)onData(d);  /* show what's cached while asking about the rest */
      provider().then(function(st){
        box.innerHTML='<svg class="i"><use href="#i-pulse"/></svg><p><b>'+esc(what)+' from DataForSEO</b>'+
          (d.missing>0?d.missing+' keyword'+(d.missing===1?'':'s')+' not cached yet'+(d.cached?' ('+d.cached+' already are)':'')+'. ':'')+
          'This makes <b class="calls">'+d.calls+' API call'+(d.calls===1?'':'s')+'</b>, billed to your DataForSEO account'+(st.balance!=null?' (balance $'+fmt(st.balance,2)+')':'')+
          '. Results are cached for 24 hours, so repeating it is free.</p><button class="btn sm p" type="button">Run lookup</button>';
        box.querySelector('button').onclick=function(){
          box.innerHTML='<span class="spin"></span><p>Asking DataForSEO for '+esc(what)+'…</p>';
          load(true).then(function(r){box.hidden=true;onData(r);prov=null}).catch(function(e){box.innerHTML='<svg class="i"><use href="#i-alert"/></svg><p class="err">'+esc(e.message)+'</p>'})};
      });
    }).catch(function(e){if(e.notConnected){box.hidden=true;return}box.innerHTML='<svg class="i"><use href="#i-alert"/></svg><p class="err">'+esc(e.message)+'</p>'});
  }
  provider().then(function(st){document.body.classList.toggle('prov-on',!!st.ok);
    [].forEach.call(document.querySelectorAll('.side-nav em[title]'),function(em){em.title=st.ok?'Live data from DataForSEO':'Needs a data provider'})});
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

  /* "Download report": the results on screen saved as one self-contained HTML file, styled like the app,
     that opens offline and prints to PDF. opt: {title, file, meta:[[label,value]…]} */
  function report(el,opt){
    function txt(p){return fetch(p).then(function(r){return r.ok?r.text():''}).catch(function(){return ''})}
    return Promise.all([txt('assets/app.css'),txt('assets/scoutly-mark.svg')]).then(function(r){
      var c=el.cloneNode(true),now=new Date(),day=new Date(now-now.getTimezoneOffset()*6e4).toISOString().slice(0,10);  /* local date */
      [].forEach.call(c.querySelectorAll('button.btn,input,select,textarea,script,[hidden],.seg,.no-report'),function(n){n.remove()});
      /* other buttons are content (e.g. Site Audit's category cards): keep them as plain blocks */
      [].forEach.call(c.querySelectorAll('button'),function(b){var d=document.createElement('div');d.className=b.className;d.innerHTML=b.innerHTML;b.replaceWith(d)});
      [].forEach.call(c.querySelectorAll('.spin'),function(s){var p=s.parentNode;if(p)p.innerHTML='<p class="meta-note">Still loading when this report was made. Download it again once it’s ready.</p>'});
      [].forEach.call(c.querySelectorAll('details'),function(d){d.open=true});
      /* links and images point at the live site, not at the downloaded file's folder */
      [].forEach.call(el.querySelectorAll('a[href],img[src]'),function(n,i){var k=c.querySelectorAll('a[href],img[src]')[i];if(k)k.setAttribute(n.tagName==='A'?'href':'src',n.tagName==='A'?n.href:n.src)});
      [].forEach.call(c.querySelectorAll('a[href]'),function(a){a.target='_blank';a.rel='noopener noreferrer'});
      var page=[].map.call(document.querySelectorAll('head style'),function(s){return s.textContent}).join('\n'),
        sprite=(document.querySelector('body>svg[aria-hidden="true"]')||{}).outerHTML||'',
        meta=(opt.meta||[]).filter(function(m){return m[1]}).concat([['Generated',now.toLocaleString()]]);
      var html='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+esc(opt.title)+' — Scoutly</title>'+
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&family=Tektur:wght@400;500;600&display=swap">'+
        '<style>'+r[0]+'\n'+page+'\n'+
        'body{padding:32px 16px 48px}body:after{display:none}.rp{max-width:1200px;margin:0 auto}'+
        '.rp-h{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px;padding-bottom:18px;border-bottom:2px solid var(--ink)}'+
        '.rp-h .logo svg{width:30px;height:30px}.rp-h h1{margin-top:14px;font:500 clamp(24px,3vw,36px)/1.05 var(--D);text-transform:uppercase}'+
        '.rp-m{display:grid;gap:4px;font:400 12px/1.5 var(--M);color:var(--sl);text-align:right}.rp-m b{color:var(--ink);font-weight:600;overflow-wrap:anywhere}'+
        '.rp-f{margin-top:28px;padding-top:14px;border-top:1px solid var(--ln);font:400 11px/1.6 var(--M);color:var(--mu)}'+
        '@media(max-width:640px){.rp-m{text-align:left}}'+
        '@media print{body{padding:0;background:#fff}.panel,.kpi,.idea,.fd,tr{break-inside:avoid}*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}'+
        '</style></head><body>'+sprite+'<div class="rp"><header class="rp-h"><div><span class="logo">'+r[1].replace(/<\?xml[^>]*>/,'')+'<span>Scoutly</span></span><h1>'+esc(opt.title)+'</h1></div>'+
        '<div class="rp-m">'+meta.map(function(m){return '<span>'+esc(m[0])+': <b>'+esc(m[1])+'</b></span>'}).join('')+'</div></header>'+
        c.innerHTML+'<p class="rp-f">Generated by Scoutly from public data on '+esc(now.toLocaleDateString())+'. Results reflect the pages as they were when checked. Print this file or save it as PDF from your browser.</p></div></body></html>';
      var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([html],{type:'text/html'}));
      a.download='scoutly-'+opt.file+'-'+day+'.html';document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},500);
      say('Report downloaded. Open it in a browser to read, print or save as PDF.');
    });
  }
  function slug(s){return String(s||'').replace(/^https?:\/\/(www\.)?/,'').replace(/[^\w.-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,60)||'report'}

  window.Scoutly={user:user,say:say,closeNav:closeNav,read:read,write:write,showSite:showSite,esc:esc,api:api,post:post,provider:provider,paid:paid,fmt:fmt,kdTag:kdTag,ago:ago,csv:csv,lists:lists,saveLists:saveLists,addToList:addToList,qs:qs,report:report,slug:slug};
})();
