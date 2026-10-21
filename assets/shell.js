/* Scoutly app shell: icon sprite, sidebar and top bar, written once for every app page.
   Load it as the first thing in <body>; call Shell.top() as the first thing inside .main.
   It runs while the page is parsing, so the sidebar is there on first paint (no flash). */
(function(){
  var I={
    dash:'<path d="M4 4h7v7H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 13h7v7H4z"/>',
    proj:'<path d="M3 6h7l2 2h9v11H3z"/>',
    rep:'<path d="M6 3h9l4 4v14H6zM9 12h7M9 16h7M9 8h3"/>',
    audit:'<circle cx="11" cy="11" r="6"/><path d="M16 16l5 5M8.5 11l2 2 3.5-4"/>',
    link:'<path d="M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 0 0-5.6-5.6l-1 1M14 10a4 4 0 0 0-5.6 0l-3 3a4 4 0 0 0 5.6 5.6l1-1"/>',
    broken:'<path d="M9 15l-2 2a3 3 0 0 1-4-4l2-2M15 9l2-2a3 3 0 0 1 4 4l-2 2M4 4l3 3M17 17l3 3M9 3v3M3 9h3M15 21v-3M21 15h-3"/>',
    speed:'<path d="M4 18a8 8 0 1 1 16 0M12 18l4-6"/>',
    key:'<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3M14 9l2 2"/>',
    comp:'<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    ai:'<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
    geo:'<path d="M4 5h16v11H9l-5 4zM8 9h8M8 12h5"/>',
    set:'<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    out:'<path d="M14 4H5v16h9M10 12h11M17 8l4 4-4 4"/>',
    bell:'<path d="M6 17V11a6 6 0 0 1 12 0v6l2 2H4zM10 21h4"/>',
    menu:'<path d="M3 6h18M3 12h18M3 18h18"/>',
    cal:'<path d="M4 6h16v14H4zM4 10h16M8 3v5M16 3v5"/>',
    clock:'<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    page:'<path d="M6 3h9l4 4v14H6zM14 3v5h5"/>',
    alert:'<path d="M12 3l10 18H2zM12 10v5M12 17.5v.5"/>',
    globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    pulse:'<path d="M2 12h5l2-5 4 10 2-5h7"/>',
    down:'<path d="M6 9l6 6 6-6"/>',
    search:'<circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/>',
    rocket:'<path d="M14 4c3-1 6-1 6-1s0 3-1 6l-6 6-4-4zM9 11l-4 1-2 3 4 1M13 15l-1 4-3 2-1-4M14.5 9.5h.01M6 18l-2 2"/>',
    pin:'<path d="M12 21s-7-6.5-7-12a7 7 0 0 1 14 0c0 5.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    edit:'<path d="M4 20h4L19 9l-4-4L4 16zM13 7l4 4"/>',
    trash:'<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>',
    target:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    build:'<path d="M5 21V4h10v17M15 9h4v12M3 21h18M8 8h4M8 12h4M8 16h4"/>',
    x:'<path d="M6 6l12 12M18 6L6 18"/>',
    wand:'<path d="M4 20L16 8M14 4v3M18 6h3M19 2l-1 2M12 2l1 2M18 10l2 1"/>',
    gap:'<path d="M3 6h7v12H3zM14 6h7v12h-7M10 12h4"/>',
    list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1"/>',
    vs:'<path d="M4 4h6v16H4zM14 9h6v11h-6"/>',
    chart:'<path d="M3 20h18M5 16l4-5 4 3 6-8"/>',
    lock:'<path d="M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4"/>',
    pen:'<path d="M3 21l3-1 12-12-2-2L4 18zM14 6l2 2M13 21h8"/>',
    bulb:'<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/>',
    trend:'<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>',
    upload:'<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    dl:'<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    check:'<path d="M4 12l5 5L20 6"/>',
    star:'<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    filter:'<path d="M3 5h18l-7 8v6l-4 2v-8z"/>'
  };
  var sprite='<svg width="0" height="0" style="position:absolute" aria-hidden="true">'+Object.keys(I).map(function(k){return '<symbol id="i-'+k+'" viewBox="0 0 24 24">'+I[k]+'</symbol>'}).join('')+'</svg>';

  /* Semrush-style SEO toolkit. lock: needs a paid data provider (the page shows locked.html without one). soon: not built yet. */
  var NAV=[
    ['SEO Services',[
      ['dashboard.html','search','SEO Dashboard'],
      ['campaigns.html','rocket','SEO Campaigns'],
      ['backlink-audit.html','link','Backlink'],
      ['local-seo.html','pin','Local SEO']]],
    ['Competitive research',[
      ['domain-overview.html','globe','Domain Overview'],
      ['domain-vs-domain.html','vs','Domain vs Domain'],
      ['organic-research.html','chart','Organic Research','lock'],
      ['traffic-analytics.html','trend','Traffic Analytics','lock']]],
    ['Keyword research',[
      ['keyword-overview.html','key','Keyword Overview'],
      ['keyword-magic.html','wand','Keyword Magic Tool'],
      ['keyword-gap.html','gap','Keyword Gap'],
      ['keyword-manager.html','list','Keyword Manager']]],
    ['Link building',[
      ['backlink-analytics.html','link','Backlink Analytics','lock'],
      ['backlink-gap.html','gap','Backlink Gap','lock']]],
    ['On-page & tech SEO',[
      ['audit.html','audit','Site Audit'],
      ['onpage-checker.html','check','On-Page SEO Checker'],
      ['writing-assistant.html','pen','SEO Writing Assistant'],
      ['topic-research.html','bulb','Topic Research']]],
    ['Rank tracking',[
      ['position-tracking.html','target','Position Tracking']]],
    ['AI visibility',[
      ['ai-search.html','ai','AI Search Visibility'],
      ['aeo-checker.html','geo','AEO · GEO Checker']]],
    ['Account',[
      ['reports.html','rep','Reports'],
      ['settings.html','set','Settings']]]
  ];
  var here=location.pathname.split('/').pop()||'dashboard.html',q=location.search;
  /* locked.html stands in for a provider tool when no provider is connected; highlight that tool */
  var LOCKED={organic:'organic-research.html',traffic:'traffic-analytics.html',backlinks:'backlink-analytics.html','backlink-gap':'backlink-gap.html'};
  if(here==='locked.html'){var lt=(q.match(/tool=([\w-]+)/)||[])[1];here=LOCKED[lt]||LOCKED.organic;q=''}
  function isCurrent(href){var h=href.split('?');return h[0]===here&&(!h[1]||q.indexOf(h[1])>-1)}
  var nav=NAV.map(function(g){return '<h2>'+g[0]+'</h2>'+g[1].map(function(it){
    var attrs=it[3]==='soon'?' data-soon="'+it[4]+'"':'',badge=it[3]==='lock'?'<em title="Needs a data provider">API</em>':it[3]==='soon'?'<em>Soon</em>':'';
    return '<a href="'+it[0]+'"'+attrs+(isCurrent(it[0])?' aria-current="page"':'')+'><svg class="i"><use href="#i-'+it[1]+'"/></svg>'+it[2]+badge+'</a>'}).join('')}).join('');
  var aside='<aside class="side" id="side" aria-label="Workspace"><div class="side-logo"><a class="logo" href="./" aria-label="Scoutly home"><img src="assets/scoutly-mark.svg" alt="" width="30" height="30"><span>Scoutly</span></a></div>'+
    '<nav class="side-nav" aria-label="SEO toolkit">'+nav+'<a href="#" id="logout"><svg class="i"><use href="#i-out"/></svg>Log out</a></nav><p class="side-foot">Early access · v0.2</p></aside><div class="scrim" id="scrim"></div>';
  document.currentScript.insertAdjacentHTML('beforebegin',sprite+aside);

  var TOP='<header class="top"><button class="icon-b menu-b" id="menuB" type="button" aria-label="Open menu" aria-expanded="false" aria-controls="side"><svg class="i"><use href="#i-menu"/></svg></button>'+
    '<span class="status" id="status"><i aria-hidden="true"></i><span id="statusT">No website yet</span></span>'+
    '<button class="proj" type="button" id="projB"><span id="projT">+ Add a website</span></button>'+
    '<div class="top-r"><button class="icon-b" type="button" aria-label="Alerts" data-soon="Alerts"><svg class="i"><use href="#i-bell"/></svg></button>'+
    '<div class="who"><span class="av" id="av" aria-hidden="true"></span><div><b id="whoName"></b><small id="whoMail"></small></div></div></div></header>';
  window.Shell={top:function(){document.currentScript.insertAdjacentHTML('beforebegin',TOP)}};

  /* default country from Settings (the account's scoutly.prefs.gl) for any tool with a #gl picker, unless the URL sets one */
  document.addEventListener('DOMContentLoaded',function(){
    var sel=document.getElementById('gl'),gl=null;if(!sel||/[?&]gl=/.test(location.search))return;
    try{gl=((JSON.parse(document.getElementById('scoutly-boot').textContent).data||{})['scoutly.prefs']||{}).gl}catch(_){}
    if(gl&&[].some.call(sel.options,function(o){return o.value===gl}))sel.value=gl;
  });
})();
