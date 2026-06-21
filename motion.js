/* ==========================================================================
   MUSTANG ANALYTICS — motion.js ("Instrument Warm-Up, Once" v1.0)
   Data-attribute API (see overhaul MANIFEST). Progressive enhancement:
   HTML is ALWAYS final-state. If GSAP fails to load, or the visitor prefers
   reduced motion, or this isn't the first view of this path this session,
   nothing animates and the page is simply correct.
   Load order (defer, after </footer>): gsap.min.js, ScrollTrigger.min.js
   (optional), then this file.
   ========================================================================== */
(function () {
  'use strict';

  window.mwScan = function () {};            /* safe no-op until boot succeeds */

  var XBASE = 16;                            /* must match .mw-x height in site.css */
  var EASE = 'power2.out';

  function ready(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn, { once: true });
    } else { fn(); }
  }

  ready(function () {
    if (!window.gsap) return;                /* CDN failed: final-state HTML stands */
    var gsap = window.gsap;
    var ST = window.ScrollTrigger;
    if (ST) gsap.registerPlugin(ST);

    /* Session gate: warm-up runs once per path per session. Flag is set
       immediately at start, so a mid-warm-up reload still skips next time. */
    var first = false;
    try {
      var key = 'mw:' + location.pathname;
      first = !sessionStorage.getItem(key);
      sessionStorage.setItem(key, '1');
    } catch (e) { first = true; }

    var mm = gsap.matchMedia();
    mm.add('(prefers-reduced-motion: no-preference)', function () {
      if (first) warmup(document);
      /* For fetch-rendered DOM (business-activity): call once after first paint. */
      window.mwScan = function (root) { if (first) warmup(root || document); };

      var offX = null;
      if (window.matchMedia('(hover:hover) and (pointer:fine)').matches) {
        offX = crosshair();
      }
      return function () {                   /* PRM flipped on mid-session */
        window.mwScan = function () {};
        if (offX) offX();
        if (ST) ST.getAll().forEach(function (t) { t.kill(); });
      };
    });
    /* PRM-reduce branch intentionally absent: do nothing, HTML is final. */

    /* ------------------------------ warm-up ------------------------------ */

    function attr(el, name, def) {
      var v = parseFloat(el.getAttribute(name));
      return isNaN(v) ? def : v;
    }

    function warmup(root) {
      if (root.nodeType === 9) root = root.documentElement;
      var els = root.querySelectorAll('[data-mw]');
      var groups = {}, rises = [];
      Array.prototype.forEach.call(els, function (el) {
        if (el.dataset.mwDone) return;       /* never re-animate (refreshes etc.) */
        el.dataset.mwDone = '1';
        var type = el.getAttribute('data-mw');
        if (type === 'row') return;          /* interaction verb, handled below */
        var d = attr(el, 'data-mw-delay', 0);
        var g = el.getAttribute('data-mw-group');
        if (g) { groups[g] = groups[g] || 0; d += groups[g] * 0.06; groups[g]++; }
        if (type === 'count')      count(el, d);
        else if (type === 'draw')  draw(el, d, attr(el, 'data-mw-dur', 0.8));
        else if (type === 'sweep') sweep(el, d);
        else if (type === 'rule')  rule(el, d, attr(el, 'data-mw-dur', 0.6));
        else if (type === 'blink') blink(el, d);
        else if (type === 'fade')  fade(el, d, attr(el, 'data-mw-dur', 0.4));
        else if (type === 'rise')  rises.push(el);
      });
      riseAll(rises);
    }

    function riseAll(els) {
      if (!els.length) return;
      var go = function (targets) {
        gsap.fromTo(targets, { opacity: 0, y: 12 }, {
          opacity: 1, y: 0, duration: 0.45, ease: EASE, stagger: 0.06,
          delay: function (i, t) { return attr(t, 'data-mw-delay', 0); },
          clearProps: 'opacity,transform'
        });
      };
      if (ST) {
        gsap.set(els, { opacity: 0 });
        ST.batch(els, { start: 'top 88%', once: true,
          onEnter: function (batch) { go(batch); } });
      } else { go(els); }
    }

    /* count: text already holds the exact final value; snapshot, count from 0
       preserving commas/decimals/prefix/suffix, restore original verbatim. */
    function count(el, d) {
      var orig = el.textContent;
      var m = orig.match(/-?\d[\d,]*(?:\.\d+)?/);
      if (!m) return;
      var s = m[0];
      var dec = (s.split('.')[1] || '').length;
      var comma = s.indexOf(',') > -1;
      var toAttr = el.getAttribute('data-mw-to');
      var target = toAttr !== null ? parseFloat(toAttr) : parseFloat(s.replace(/,/g, ''));
      if (isNaN(target)) return;
      var pre = orig.slice(0, m.index), post = orig.slice(m.index + s.length);
      var o = { v: 0 };
      gsap.to(o, { v: target, duration: 0.9, delay: d, ease: 'power1.inOut',
        onUpdate: function () {
          var t = comma
            ? o.v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })
            : o.v.toFixed(dec);
          el.textContent = pre + t + post;
        },
        onComplete: function () { el.textContent = orig; }
      });
    }

    function strokes(el) {
      return el.getTotalLength
        ? [el]
        : Array.prototype.slice.call(el.querySelectorAll('path,line,polyline,circle'));
    }

    function drawOne(p, d, dur) {
      var L; try { L = p.getTotalLength(); } catch (e) { return; }
      if (!L) return;
      var prevDA = p.style.strokeDasharray;  /* restore exactly on complete */
      p.style.strokeDasharray = L + ' ' + L;
      p.style.strokeDashoffset = L;
      gsap.to(p, { strokeDashoffset: 0, duration: dur, delay: d, ease: EASE,
        onComplete: function () {
          p.style.strokeDasharray = prevDA || '';
          p.style.strokeDashoffset = '';
        }
      });
    }

    function draw(el, d, dur) {
      strokes(el).forEach(function (p) { drawOne(p, d, dur); });
    }

    function sweep(el, d) {                  /* donut segments, staggered 0.1s */
      strokes(el).forEach(function (p, i) { drawOne(p, d + i * 0.1, 0.8); });
    }

    function rule(el, d, dur) {
      var y = el.getAttribute('data-mw-axis') === 'y';
      var origin = el.getAttribute('data-mw-origin') || (y ? 'top' : 'left');
      var o = { left: '0% 50%', right: '100% 50%', top: '50% 0%', bottom: '50% 100%' }[origin] || '0% 50%';
      var from = { transformOrigin: o }, to = { duration: dur, delay: d, ease: EASE, clearProps: 'transform,transformOrigin' };
      from[y ? 'scaleY' : 'scaleX'] = 0;
      to[y ? 'scaleY' : 'scaleX'] = 1;
      gsap.fromTo(el, from, to);
    }

    function blink(el, d) {                  /* dim->steady, two low-contrast cycles */
      gsap.fromTo(el, { opacity: 0.3 }, { opacity: 1, duration: 0.3, delay: d,
        repeat: 2, yoyo: true, ease: 'none', clearProps: 'opacity' });
    }

    function fade(el, d, dur) {              /* e.g. dashed forecast paths */
      gsap.fromTo(el, { opacity: 0 }, { opacity: 1, duration: dur, delay: d,
        ease: EASE, clearProps: 'opacity' });
    }

    /* ----------------------- crosshair (persistent) -----------------------
       data-mw="row" on a <tbody> (or any container whose direct children are
       rows). Pure delegation on document, so regenerated tables (arbitrage
       every 2 min, business-activity refresh) need no re-init. One layout
       read batch per container on first hover; zero reads while moving.
       Cells/numbers are never animated — only the aria-hidden overlay moves. */
    function crosshair() {
      var cache = new WeakMap();             /* container -> {ov, pos} */

      function setup(c) {
        var info = cache.get(c);
        if (info) return info;
        var host = c.closest('.tablewrap');
        if (!host) {
          var t = c.closest('table');
          host = t ? t.parentElement : c.parentElement;
        }
        if (!host) return null;
        host.classList.add('mw-host');
        var ov = document.createElement('div');
        ov.className = 'mw-x';
        ov.setAttribute('aria-hidden', 'true');
        host.appendChild(ov);
        info = { ov: ov, pos: new WeakMap(), on: false, host: host };
        cache.set(c, info);
        recache(c, info);
        return info;
      }

      function recache(c, info) {            /* one batched read pass */
        var hr = info.host.getBoundingClientRect();
        Array.prototype.forEach.call(c.children, function (r) {
          var rr = r.getBoundingClientRect();
          info.pos.set(r, [rr.top - hr.top, rr.height]);
        });
      }

      function over(e) {
        var c = e.target.closest && e.target.closest('[data-mw="row"]');
        if (!c) return;
        var row = e.target;
        while (row && row.parentElement !== c) row = row.parentElement;
        if (!row) return;
        var info = setup(c);
        if (!info) return;
        if (!info.pos.has(row)) {            /* rows replaced in-place: re-read once */
          recache(c, info);
          if (!info.pos.has(row)) return;
        }
        var p = info.pos.get(row);
        gsap.to(info.ov, { y: p[0], scaleY: p[1] / XBASE, opacity: 1,
          duration: info.on ? 0.25 : 0.18, ease: EASE, overwrite: true });
        info.on = true;
      }

      function out(e) {
        var c = e.target.closest && e.target.closest('[data-mw="row"]');
        if (!c) return;
        if (e.relatedTarget && c.contains(e.relatedTarget)) return;
        var info = cache.get(c);
        if (!info || !info.on) return;
        info.on = false;
        gsap.to(info.ov, { opacity: 0, duration: 0.18, ease: EASE, overwrite: true });
      }

      document.addEventListener('pointerover', over);
      document.addEventListener('pointerout', out);
      return function () {                   /* teardown for PRM flip */
        document.removeEventListener('pointerover', over);
        document.removeEventListener('pointerout', out);
        Array.prototype.forEach.call(document.querySelectorAll('.mw-x'),
          function (n) { n.parentNode && n.parentNode.removeChild(n); });
      };
    }
  });
})();
