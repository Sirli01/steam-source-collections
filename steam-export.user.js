// ==UserScript==
// @name         导出 Steam 许可与游戏库（steam-source-collections）
// @namespace    steam-source-collections
// @version      4.0
// @description  在 Steam 许可页一键导出分组工具需要的 JSON 数据包；自动翻完所有分页，全程本机处理，不上传任何数据
// @match        https://store.steampowered.com/account/licenses*
// @grant        none
// ==/UserScript==
/*
 * 两种用法：
 *   1) 装了 Tampermonkey / Violentmonkey 时，把本文件作为用户脚本安装；
 *      打开许可页后右上角会出现「导出许可数据」按钮。
 *   2) 不装扩展：把图形界面里的蓝色书签按钮拖到书签栏，在许可页点一下。
 *      书签里内嵌的就是下面这段代码。
 *
 * 它会翻完许可页的所有分页、补齐游戏名称和游玩时长，并读取家人通过 Steam
 * 家庭组借给你的游戏；这些数据只存在于已登录的 Steam 页面上。
 *
 * 少数 DLC / 已下架项目本来就查不到名称，图形界面点「一键统计」会自动用
 * 本机 Steam 缓存把它们按礼包包归组，不需要重新导出。
 *
 * 脚本只访问 Steam 自己的页面和接口，不读取登录 Cookie、不向任何第三方发送数据，
 * 结果只下载到你本机。
 */
(function () {
  'use strict';

  var HOST_OK = location.hostname === 'store.steampowered.com';
  var PAGE_OK = location.pathname.indexOf('/account/licenses') === 0;
  if (!HOST_OK || !PAGE_OK) {
    alert('请先打开并登录 https://store.steampowered.com/account/licenses/ ，再点「导出许可数据」。');
    return;
  }
  if (window.__sscRunning) {
    return;
  }
  var oldPanel = document.getElementById('ssc-panel');
  if (oldPanel && oldPanel.parentNode) {
    oldPanel.parentNode.removeChild(oldPanel);
  }
  var IS_USERSCRIPT = typeof GM_info !== 'undefined';

  // ---------- 悬浮面板 ----------
  var panel = document.createElement('div');
  panel.id = 'ssc-panel';
  panel.style.cssText = 'position:fixed;z-index:2147483647;top:18px;right:18px;width:380px;' +
    'background:#182230;color:#eaf0f8;border:1px solid #3a4a60;border-radius:12px;' +
    'padding:15px 16px;box-shadow:0 12px 40px rgba(0,0,0,.45);' +
    'font:13px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
  panel.innerHTML = '<div style="font-weight:700;font-size:14px;margin-bottom:6px">Steam 许可导出 <span style="font-weight:400;color:#93a3b8;font-size:11px">v4</span></div>' +
    '<div id="ssc-msg" style="white-space:pre-line">准备中…</div>' +
    '<div id="ssc-bar" style="height:5px;background:#2b3a4e;border-radius:3px;margin-top:10px;overflow:hidden">' +
    '<div id="ssc-fill" style="height:100%;width:0;background:#5b9bff;transition:width .2s"></div></div>' +
    '<div style="color:#93a3b8;font-size:11px;margin-top:9px">自动翻完所有分页、补全名称并读取家庭共享，首次可能要等 30～90 秒；期间请勿关闭页面。<br>只在本机处理，不上传数据。</div>';
  document.body.appendChild(panel);
  var msg = panel.querySelector('#ssc-msg');
  var fill = panel.querySelector('#ssc-fill');
  function say(text, pct) {
    msg.textContent = text;
    if (typeof pct === 'number') fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function decodeAttr(s) {
    return String(s || '').replace(/&quot;/g, '"').replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&#39;/g, "'");
  }

  // ---------- 1. 解析许可表（对当前 DOM 与 fetch 回来的 HTML 通用） ----------
  function cleanCell(el) {
    var copy = el.cloneNode(true);
    var junk = copy.querySelectorAll('.free_license_remove_link, a[href*="RemoveFreeLicense"]');
    for (var i = 0; i < junk.length; i++) junk[i].parentNode.removeChild(junk[i]);
    return (copy.textContent || '').replace(/\s+/g, ' ').trim();
  }
  function parseRow(tr) {
    var tds = [].slice.call(tr.querySelectorAll('td'));
    if (tds.length < 3) return null;
    var dateTd = null, acqTd = null, others = [];
    for (var j = 0; j < tds.length; j++) {
      var cls = tds[j].className || '';
      if (cls.indexOf('license_date_col') >= 0) dateTd = tds[j];
      else if (cls.indexOf('license_acquisition_col') >= 0) acqTd = tds[j];
      else if (cls.indexOf('steamdb_license_id_col') < 0) others.push(tds[j]);
    }
    var date = dateTd ? cleanCell(dateTd) : '';
    var method = acqTd ? cleanCell(acqTd) : '';
    var name = others.length ? cleanCell(others[0]) : '';
    if ((!date || !method) && tds.length >= 3) {
      var vals = tds.map(cleanCell);
      if (!date && !method) { date = vals[0]; name = vals[1]; method = vals[2]; }
      else { date = date || vals[0]; name = name || vals[1]; method = method || vals[2]; }
    }
    if (!name) return null;
    var html = tr.innerHTML;
    var pm = html.match(/RemoveFreeLicense\s*\(\s*['"]?(\d+)/i) ||
             html.match(/\/sub\/(\d+)/i) ||
             html.match(/packageids?[=/](\d+)/i) ||
             html.match(/RemovePackage\s*\(\s*['"]?(\d+)/i) ||
             html.match(/data-packageid\s*=\s*['"]?(\d+)/i) ||
             html.match(/licens(?:e)?[_-]?id["'=:\s]+(\d+)/i);
    var appids = [];
    var links = tr.querySelectorAll('a[href]');
    for (var k = 0; k < links.length; k++) {
      var am = links[k].getAttribute('href').match(/\/app\/(\d+)/i);
      if (am && appids.indexOf(Number(am[1])) < 0) appids.push(Number(am[1]));
    }
    return { date: date, name: name, method: method, packageid: pm ? pm[1] : '', appids: appids };
  }
  function rowsOf(doc) {
    return [].slice.call(doc.querySelectorAll('table.account_table tr')).map(parseRow)
      .filter(function (r) { return r && r.name; });
  }
  function fingerprint(r) {
    return [r.date, r.name, r.method, r.packageid, (r.appids || []).join(',')].join('|');
  }
  function nextHref(doc) {
    var a = doc.querySelector('a.license_paginator_next');
    if (!a) return '';
    var h = a.getAttribute('href') || '';
    if (!h) return '';
    try { return new URL(h, location.href).href; } catch (e) { return ''; }
  }
  function declaredTotal(doc) {
    var c = doc.querySelector('.license_paginator_ctn');
    var text = (c ? c.textContent || '' : '').replace(/,/g, '');
    var m = text.match(/共\s*(\d+)\s*[个個項项]/) || text.match(/\bof\s+(\d+)\b/i);
    return m ? Number(m[1]) : 0;
  }

  // ---------- 2. 自动翻完全部分页 ----------
  async function fetchAllLicenses(onProgress) {
    var doc = document;
    /* 若当前 URL 带着分页游标，先回到干净的第一页 */
    if (/continuationToken|offset/i.test(location.search)) {
      var first = await fetch('https://store.steampowered.com/account/licenses/', { credentials: 'include' });
      doc = new DOMParser().parseFromString(await first.text(), 'text/html');
    }
    var total = declaredTotal(doc);
    var all = [], seen = Object.create(null), pages = 1, visited = Object.create(null);
    visited[location.href] = 1;
    function absorb(pageRows) {
      var local = Object.create(null), fresh = 0;
      pageRows.forEach(function (r) {
        var fp = fingerprint(r);
        var n = local[fp] = (local[fp] || 0) + 1;
        var key = fp + '#' + n;
        if (!seen[key]) { seen[key] = 1; all.push(r); fresh++; }
      });
      return fresh;
    }
    absorb(rowsOf(doc));
    var url = nextHref(doc);
    var stall = 0, stopReason = 'no-next-link';
    while (url && pages < 500) {
      if (visited[url]) { stopReason = 'paginator-loop'; break; }
      visited[url] = 1;
      var res = await fetch(url, { credentials: 'include' });
      if (!res.ok) { stopReason = 'http-' + res.status; break; }
      var sub = new DOMParser().parseFromString(await res.text(), 'text/html');
      var pr = rowsOf(sub);
      if (!pr.length) { stopReason = 'empty-page'; break; }
      var fresh = absorb(pr);
      pages++;
      onProgress(pages, all.length, total);
      /* 单页没有新行不一定是回绕，允许连续两页空转才收工 */
      if (fresh === 0) { stall++; if (stall >= 2) { stopReason = 'no-new-rows'; break; } }
      else stall = 0;
      if (total && all.length >= total) { stopReason = 'reached-declared-total'; break; }
      var nxt = nextHref(sub);
      if (!nxt) { stopReason = 'no-next-link'; break; }
      url = nxt;
      await sleep(120);
    }
    return { rows: all, pages: pages, total: total, stopReason: stopReason };
  }

  // ---------- 3. 商店页配置 + 游戏库 ----------
  function readConfig() {
    var el = document.getElementById('application_config');
    var ui = {}, sc = {};
    if (el) {
      try { ui = JSON.parse(decodeAttr(el.getAttribute('data-userinfo')) || '{}'); } catch (e) { }
      try { sc = JSON.parse(decodeAttr(el.getAttribute('data-store_user_config')) || '{}'); } catch (e) { }
    }
    var sid = String(ui.steamid || window.g_steamID || '');
    if (!/^\d{17}$/.test(sid)) {
      var m = document.documentElement.innerHTML.match(/g_steamID\s*=\s*"?(\d{17})/);
      sid = m ? m[1] : '';
    }
    return { sid: sid, token: String(sc.webapi_token || '') };
  }
  async function loadConfig() {
    var c = readConfig();
    if (c.sid && c.token) return c;
    try {
      var r = await fetch('https://store.steampowered.com/', { credentials: 'include' });
      var t = await r.text();
      var m1 = t.match(/id="application_config"[^>]*data-store_user_config="([^"]*)"/);
      if (m1) { try { var sc = JSON.parse(decodeAttr(m1[1])); c.token = String(sc.webapi_token || ''); } catch (e) { } }
      if (!c.token) { var m2 = t.match(/"webapi_token"\s*:\s*"([^"]+)"/); if (m2) c.token = m2[1]; }
      if (!c.sid) { var m3 = t.match(/"steamid"\s*:\s*"(\d{17})"/); if (m3) c.sid = m3[1]; }
    } catch (e) { }
    return c;
  }

  async function fetchOwnedGames(cfg) {
    if (!cfg.sid || !cfg.token) return { games: null, diag: 'missing-webapi-token' };
    var last = 'no-response';
    var versions = ['v1', 'v0001'];
    for (var i = 0; i < versions.length; i++) {
      var u = 'https://api.steampowered.com/IPlayerService/GetOwnedGames/' + versions[i] + '/' +
        '?access_token=' + encodeURIComponent(cfg.token) +
        '&steamid=' + encodeURIComponent(cfg.sid) +
        '&include_appinfo=1&include_played_free_games=1&format=json';
      try {
        var r = await fetch(u, { credentials: 'omit' });
        if (!r.ok) { last = versions[i] + ' http-' + r.status; continue; }
        var j = await r.json();
        var g = j && j.response && j.response.games;
        if (g && g.length) return { games: g, diag: versions[i] + ' ok' };
        last = versions[i] + ' empty';
      } catch (e) { last = versions[i] + ' error'; }
    }
    return { games: null, diag: last };
  }
  async function fetchUserData() {
    try {
      var r = await fetch('/dynamicstore/userdata/', { credentials: 'include' });
      return await r.json();
    } catch (e) { return null; }
  }

  /* ---------- 4. Steam Families：借来的游戏 ----------
     许可页和 GetOwnedGames 都只列出自己账号拥有的游戏，所以⑤组一直是 0。
     IFamilyGroupsService 是未公开接口，但用商店页自己的 webapi_token 就能调用：
     先拿 family_groupid，再列 GetSharedLibraryApps。不需要 API Key，
     也不需要家人的账号或密码；失败时只影响⑤组，不会破坏其它数据。 */
  async function fetchFamilyShared(cfg, ownSet) {
    var out = { appids: [], names: {}, groupid: '', diag: 'not-run' };
    if (!cfg.token) { out.diag = 'missing-webapi-token'; return out; }
    var base = 'https://api.steampowered.com/IFamilyGroupsService/';
    var tok = encodeURIComponent(cfg.token);
    var gid = '';
    try {
      var r1 = await fetch(base + 'GetFamilyGroupForUser/v1/?access_token=' + tok +
        '&include_family_group_response=true', { credentials: 'omit' });
      if (!r1.ok) { out.diag = 'group-http-' + r1.status; }
      else {
        var j1 = await r1.json();
        var resp1 = (j1 && j1.response) || {};
        gid = String(resp1.family_groupid || resp1.family_group_id || '');
      }
    } catch (e) { out.diag = 'group-request-failed'; }
    if (!gid) {
      /* 兜底：从已登录的家庭管理页里找 groupid */
      try {
        var rp = await fetch('https://store.steampowered.com/account/familymanagement/',
          { credentials: 'include' });
        var tp = await rp.text();
        var mg = tp.match(/family_groupid["'\s:=]+(\d{4,})/i) ||
                 tp.match(/groupid["'\s:=]+(\d{4,})/i);
        if (mg) gid = mg[1];
      } catch (e) { /* 保持为空，交给 diag 说明 */ }
    }
    if (!gid) {
      if (out.diag === 'not-run') out.diag = 'no-family-group';
      return out;
    }
    out.groupid = gid;
    try {
      var r2 = await fetch(base + 'GetSharedLibraryApps/v1/?access_token=' + tok +
        '&family_groupid=' + encodeURIComponent(gid) +
        '&include_own=false&include_excluded=false&include_non_games=false',
        { credentials: 'omit' });
      if (!r2.ok) { out.diag = 'shared-http-' + r2.status; return out; }
      var j2 = await r2.json();
      var apps = (j2 && j2.response && j2.response.apps) || [];
      apps.forEach(function (a) {
        if (!a || Number(a.exclude_reason || 0) !== 0) return;
        var id = Number(a.appid || a.app_id || 0);
        if (!id || ownSet[id] || out.appids.indexOf(id) >= 0) return;
        out.appids.push(id);
        if (a.name) out.names[String(id)] = String(a.name);
      });
      out.diag = 'ok';
    } catch (e) { out.diag = 'shared-request-failed'; }
    return out;
  }

  /* 先探测 appdetails 是否接受逗号分隔的批量 appids；不支持就退回逐个。 */
  async function probeBatch(endpoint, ids, key) {
    try {
      var r = await fetch('/api/' + endpoint + '?' + key + '=' + ids.join(',') + '&cc=us&l=english',
        { credentials: 'include' });
      var d = await r.json();
      var ok = 0;
      ids.forEach(function (id) { if (d && d[String(id)] && d[String(id)].success) ok++; });
      return ok >= 2;
    } catch (e) { return false; }
  }

  /* 逐个或分批补齐所有缺名称的 appid。 */
  function fetchNamesFallback(ids, batchOK, onProgress) {
    var names = {}, queue = ids.slice(), done = 0, total = queue.length;
    var diag = { requested: total, batch_supported: !!batchOK, requests: 0, got: 0 };
    return new Promise(function (resolve) {
      if (!total) { resolve({ names: names, diag: diag }); return; }
      function take() { return queue.splice(0, batchOK ? 20 : 1); }
      function worker() {
        return new Promise(function (fin) {
          (function next() {
            if (!queue.length) { fin(); return; }
            var batch = take();
            fetch('/api/appdetails?appids=' + batch.join(',') + '&cc=us&l=english',
              { credentials: 'include' })
              .then(function (r) { return r.json(); })
              .then(function (d) {
                batch.forEach(function (id) {
                  var e = d && d[String(id)];
                  if (e && e.success && e.data && e.data.name) {
                    names[String(id)] = String(e.data.name);
                    diag.got++;
                  }
                });
              })
              .catch(function () { })
              .then(function () {
                diag.requests++;
                done += batch.length;
                if (done % 100 < batch.length || !queue.length) onProgress(done, total);
                setTimeout(next, batchOK ? 110 : 65);
              });
          })();
        });
      }
      var workers = [];
      for (var i = 0; i < (batchOK ? 3 : 4); i++) workers.push(worker());
      Promise.all(workers).then(function () { resolve({ names: names, diag: diag }); });
    });
  }

  function fetchPackages(ids, batchOK, onProgress) {
    var map = {}, queue = ids.slice(), done = 0, total = queue.length;
    var diag = { requested: total, batch_supported: !!batchOK, ok: 0 };
    return new Promise(function (resolve) {
      if (!total) { resolve({ map: map, diag: diag }); return; }
      function take() { return queue.splice(0, batchOK ? 20 : 1); }
      function worker() {
        return new Promise(function (fin) {
          (function next() {
            if (!queue.length) { fin(); return; }
            var batch = take();
            fetch('/api/packagedetails?packageids=' + batch.join(',') + '&cc=us',
              { credentials: 'include' })
              .then(function (r) { return r.json(); })
              .then(function (d) {
                batch.forEach(function (pid) {
                  var e = d && d[String(pid)];
                  var apps = e && e.data && e.data.apps;
                  if (apps && apps.length) {
                    map[String(pid)] = apps.map(function (a) {
                      return { id: Number(a.id), name: a.name || '' };
                    });
                    diag.ok++;
                  }
                });
              })
              .catch(function () { })
              .then(function () {
                done += batch.length;
                if (done % 100 < batch.length || !queue.length) onProgress(done, total);
                setTimeout(next, batchOK ? 110 : 70);
              });
          })();
        });
      }
      var workers = [];
      for (var i = 0; i < 3; i++) workers.push(worker());
      Promise.all(workers).then(function () { resolve({ map: map, diag: diag }); });
    });
  }

  function download(payload) {
    var d = new Date();
    function pad(n) { return String(n).length < 2 ? '0' + n : String(n); }
    var stamp = d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
      '-' + pad(d.getHours()) + pad(d.getMinutes());
    var blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = 'steam-licenses-' + stamp + '.json';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  // ---------- 主流程 ----------
  async function run() {
    say('准备中：读取登录态配置…', 2);
    var cfg = await loadConfig();

    say('正在读取许可表（会自动翻完所有分页）…', 5);
    var lic = await fetchAllLicenses(function (p, n, t) {
      say('正在加载第 ' + p + ' 页许可…（已获得 ' + n + ' 行' + (t ? ' / 页面标注约 ' + t + ' 个' : '') + '）',
        Math.min(40, 6 + p * 2));
    });
    if (!lic.rows.length) {
      say('没读到许可记录。请确认已登录、页面已加载完（能看到许可表格），然后重新点一次书签。', 100);
      return;
    }

    say('已抓取 ' + lic.pages + ' 页 / ' + lic.rows.length + ' 行许可，正在读取游戏库…', 42);
    var ownedResult = await fetchOwnedGames(cfg);
    var games = ownedResult.games;
    var ud = await fetchUserData();

    var owned = [], seenOwned = Object.create(null);
    function pushIds(list) {
      (list || []).forEach(function (v) {
        var n = Number(v);
        if (n > 0 && !seenOwned[n]) { seenOwned[n] = 1; owned.push(n); }
      });
    }
    pushIds(ud && ud.rgOwnedApps);
    pushIds((games || []).map(function (g) { return g.appid; }));
    if (!owned.length) {
      say('没读到拥有的游戏列表，看起来这个页面没有登录。请先登录 Steam 商店再点导出。', 100);
      return;
    }

    var names = {}, times = {};
    (games || []).forEach(function (g) {
      if (!g || !g.appid) return;
      if (g.name) names[String(g.appid)] = String(g.name);
      if (g.playtime_forever != null) times[String(g.appid)] = Number(g.playtime_forever) || 0;
    });
    var webNames = Object.keys(names).length;
    var nameSource = games && games.length ? 'webapi' : 'appdetails';
    var missing = owned.filter(function (id) { return !names[String(id)]; });
    var nameDiag = { requested: 0, batch_supported: false, requests: 0, got: 0 };
    if (missing.length) {
      say('正在补全 ' + missing.length + ' 款游戏的名称…', 48);
      var nameBatch = await probeBatch('appdetails', [70, 400, 550], 'appids');
      var extra = await fetchNamesFallback(missing, nameBatch, function (d, t) {
        say('正在补充游戏名称（' + d + ' / ' + t + '）…', 48 + 22 * d / t);
      });
      Object.keys(extra.names).forEach(function (k) { names[k] = extra.names[k]; });
      nameDiag = extra.diag;
      nameSource = 'appdetails' + (nameBatch ? '-batch' : '-single');
    }

    var pids = [];
    lic.rows.forEach(function (r) {
      if (r.packageid && pids.indexOf(r.packageid) < 0) pids.push(r.packageid);
    });
    say('正在解析 ' + pids.length + ' 个礼包包的精确 AppID…', 76);
    var pkgBatch = pids.length ? await probeBatch('packagedetails', [218, 515], 'packageids') : false;
    var pkgResult = await fetchPackages(pids, pkgBatch, function (d, t) {
      say('正在解析礼包包（' + d + ' / ' + t + '）…', 76 + 14 * d / t);
    });
    var packageApps = pkgResult.map;
    var extraNames = {};
    Object.keys(packageApps).forEach(function (pid) {
      packageApps[pid].forEach(function (app) {
        if (app.id && app.name) extraNames[String(app.id)] = app.name;
      });
    });
    Object.keys(extraNames).forEach(function (id) {
      if (!names[id]) names[id] = extraNames[id];
    });
    lic.rows.forEach(function (r) {
      var hits = r.packageid && packageApps[r.packageid];
      if (hits) hits.forEach(function (app) {
        if (app.id && r.appids.indexOf(app.id) < 0) r.appids.push(app.id);
      });
    });

    say('正在读取家庭共享游戏…', 92);
    var fam = await fetchFamilyShared(cfg, seenOwned);
    Object.keys(fam.names).forEach(function (id) {
      if (!names[id]) names[id] = fam.names[id];
    });

    var diagnostics = {
      family: { status: fam.diag, groupid: fam.groupid, apps: fam.appids.length },
      webapi: ownedResult.diag,
      webapi_games: games ? games.length : 0,
      webapi_names: webNames,
      names: nameDiag,
      packages: pkgResult.diag,
      license_stop_reason: lic.stopReason,
      license_rows_with_packageid: pids.length
    };

    var payload = {
      schema: 'steam-license-export/3',
      exported_at: new Date().toISOString(),
      steamid64: cfg.sid || '',
      license_pages: lic.pages,
      license_total: lic.total,
      name_source: nameSource,
      licenses: lic.rows,
      owned_appids: owned,
      owned_packages: (ud && ud.rgOwnedPackages) || [],
      auto_grant_appids: (ud && ud.rgAutoGrantApps) || [],
      family_groupid: fam.groupid || '',
      family_shared_appids: fam.appids,
      family_shared_names: fam.names,
      app_names: names,
      playtimes: times,
      package_apps: packageApps,
      diagnostics: diagnostics
    };

    var stillMissing = owned.length - Object.keys(names).length;
    say('完成！已下载 steam-licenses-*.json：' + lic.pages + ' 页 / ' + lic.rows.length +
      ' 行许可 / ' + owned.length + ' 款游戏 / ' + Object.keys(names).length + ' 个名称' +
      (fam.appids.length ? ' / ' + fam.appids.length + ' 款家庭共享游戏' : '') +
      (Object.keys(times).length ? '（含游玩时长）' : '') + '。\n' +
      (stillMissing ? '仍有 ' + stillMissing + ' 款没名称，多是 DLC / 已下架内容，商店接口本来就不返回；不用重导，图形界面的「一键统计」会自动读本机缓存把它们对齐。\n' : '') +
      (fam.diag !== 'ok' ? '家庭共享接口本次没读到（' + fam.diag + '），⑤ 组仍可手动填清单。\n' : '') +
      '下一步：把下载的文件拖进「Steam 收藏集分组」的图形界面或试用页，结果会立刻出现（不用敲命令）。', 100);
    download(payload);
  }

  function startExport() {
    if (window.__sscRunning) return;
    window.__sscRunning = true;
    run().catch(function (err) {
      say('出错了：' + (err && err.message ? err.message : err) + '\n可以刷新许可页后重试。', 100);
    }).then(function () {
      window.__sscRunning = false;
    });
  }

  if (IS_USERSCRIPT) {
    say('已就绪：点击下方按钮开始抓取并导出全部许可数据。', 0);
    var startBtn = document.createElement('button');
    startBtn.id = 'ssc-start-btn';
    startBtn.textContent = '开始导出许可数据';
    startBtn.style.cssText = 'margin-top:10px;width:100%;padding:8px 12px;border:0;border-radius:6px;' +
      'background:#5b9bff;color:#0f172a;font-weight:700;font-size:13px;cursor:pointer';
    startBtn.onclick = function () {
      if (startBtn.parentNode) startBtn.parentNode.removeChild(startBtn);
      startExport();
    };
    panel.insertBefore(startBtn, panel.querySelector('#ssc-bar'));
  } else {
    startExport();
  }
})();
