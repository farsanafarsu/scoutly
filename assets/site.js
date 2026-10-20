/* Scoutly shared behaviour: mobile navigation and website-URL scan forms.
   Scan forms are plain GET forms to login.html, so they still work without JavaScript;
   this script adds validation, URL normalisation and status messages. */
(function () {
  var $ = function (i) { return document.getElementById(i); };

  /* navigation: hide the utility strip on scroll, burger menu on small screens */
  var nav = $('nav'), burger = $('navBurger'), links = $('navLinks');
  if (nav) addEventListener('scroll', function () { nav.classList.toggle('s', scrollY > 40); }, { passive: true });
  function menu(open) {
    if (!nav || !burger) return;
    nav.classList.toggle('open', open);
    burger.setAttribute('aria-expanded', open);
    burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
  }
  if (burger) burger.addEventListener('click', function () { menu(!nav.classList.contains('open')); });
  if (links) links.addEventListener('click', function (e) { if (e.target.closest('a')) menu(false); });
  addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && nav && nav.classList.contains('open')) { menu(false); burger.focus(); }
  });
  window.scoutlyMenu = menu;

  /* website URL validation */
  function check(raw) {
    var v = (raw || '').trim();
    if (!v) return { err: 'Enter your website URL to start, for example yourwebsite.com.' };
    if (/\s/.test(v)) return { err: 'A website address can’t contain spaces.' };
    if (!/^https?:\/\//i.test(v)) {
      if (/^[a-z][a-z0-9+.-]*:/i.test(v) && !/^[^:]+:\d/.test(v)) return { err: 'Use a web address that starts with http:// or https://.' };
      v = 'https://' + v;
    }
    var u;
    try { u = new URL(v); } catch (e) { return { err: 'That doesn’t look like a website address. Try something like example.com.' }; }
    var h = u.hostname.toLowerCase().replace(/\.$/, '');
    if (!/^([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$/.test(h))
      return { err: 'Enter a public website address with a domain name, like example.com.' };
    var path = u.pathname === '/' ? '' : u.pathname;
    return { site: h + (u.port ? ':' + u.port : '') + path };
  }
  window.scoutlyCheckUrl = check;

  [].forEach.call(document.querySelectorAll('form[data-scan]'), function (form) {
    var input = form.querySelector('input[name="site"]'), msg = $(form.getAttribute('data-msg'));
    function say(text, bad) { if (!msg) return; msg.textContent = text; msg.classList.toggle('err', !!bad); }
    input.addEventListener('input', function () { if (input.getAttribute('aria-invalid') === 'true') { input.removeAttribute('aria-invalid'); say(''); } });
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var r = check(input.value);
      if (r.err) { input.setAttribute('aria-invalid', 'true'); say(r.err, true); input.focus(); return; }
      input.removeAttribute('aria-invalid');
      form.setAttribute('aria-busy', 'true');
      say('Looks good. Taking you to set up your audit for ' + r.site + '…');
      var dest = form.getAttribute('action') + '?site=' + encodeURIComponent(r.site);
      setTimeout(function () { location.href = dest; }, 450);
    });
  });
  /* restore the button if the page is shown again from the back/forward cache */
  addEventListener('pageshow', function () {
    [].forEach.call(document.querySelectorAll('form[data-scan][aria-busy]'), function (f) { f.removeAttribute('aria-busy'); });
  });
})();
