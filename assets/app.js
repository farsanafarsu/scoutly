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
    if($('statusT'))$('statusT').textContent=site+' · audit queued';
    if($('projT'))$('projT').textContent=site;
  }
  showSite(read('scoutly.site'));
  /* pages without the add-website box send the "+ Add a website" button to the dashboard */
  if($('projB')&&!$('scanUrl'))$('projB').addEventListener('click',function(){location.href='dashboard.html'});

  if($('logout'))$('logout').addEventListener('click',function(e){e.preventDefault();
    try{['scoutly.user','scoutly.site','scoutly.campaigns'].forEach(function(k){sessionStorage.removeItem(k)})}catch(_){}
    location.href='login.html?mode=login'});

  window.Scoutly={user:user,say:say,closeNav:closeNav,read:read,write:write,showSite:showSite};
})();
