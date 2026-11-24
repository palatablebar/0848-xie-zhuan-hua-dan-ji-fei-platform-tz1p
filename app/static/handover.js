(function () {
  function $(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || data.code || ('HTTP ' + response.status));
    return data;
  }
  function profile() { var el = $('profile'); return el ? el.value : 'clean'; }
  function setStatus(text, bad) {
    var el = $('ho-status');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = bad ? '#8a2f2f' : '';
  }
  function buildBody(extra) {
    var body = { profile: profile() };
    var at = $('ho-effective').value.trim();
    if (at) body.effective_at = at;
    var mapText = $('ho-effective-map').value.trim();
    if (mapText) {
      try { body.effective_times = JSON.parse(mapText); }
      catch (e) { throw new Error('分用户生效时刻不是合法 JSON'); }
    }
    return Object.assign(body, extra || {});
  }
  function renderRecon(r) {
    if (!r) { $('ho-recon').innerHTML = ''; return; }
    var recon = r.reconciliation || {};
    var balanced = r.balanced !== undefined ? r.balanced : recon.balanced;
    var chips = [
      ['合并区间', r.group_count], ['可签发', r.issuable_count], ['暂缓', r.held_count],
      ['客户应收(分)', r.customer_total_cents],
      ['原运营商(分)', recon.operator_old_cents], ['新运营商(分)', recon.operator_new_cents],
      ['两家合计(分)', r.operator_total_cents], ['差额(分)', recon.difference_cents]
    ];
    $('ho-recon').innerHTML = chips.map(function (c) {
      return '<div class="ledger-row"><span>' + esc(c[0]) + '</span><strong>' + esc(c[1]) + '</strong></div>';
    }).join('') + '<div class="ledger-row"><span>对账</span><strong class="' +
      (balanced ? 'check-ok' : 'check-bad') + '">' + (balanced ? '平衡到分' : '不平衡') + '</strong></div>';
  }
  function renderGroups(settlement) {
    var groups = (settlement && (settlement.groups || settlement.records)) || [];
    $('ho-head').innerHTML = '<tr><th>用户|方向#段</th><th>通话</th><th>状态</th><th>客户应收(分)</th>' +
      '<th>原运营商 秒/分/金额</th><th>新运营商 秒/分/金额</th><th>对账</th></tr>';
    $('ho-body').innerHTML = groups.length ? groups.map(function (g) {
      var held = g.status === 'HELD';
      return '<tr>' +
        '<td>' + esc(g.group_key || g.label) + '</td>' +
        '<td><small>' + esc((g.call_ids || []).join(',')) + '</small></td>' +
        '<td><span class="verdict" data-v="' + (held ? 'REVIEW' : 'PASS') + '">' + esc(g.status) +
          '</span><br><small>' + esc(g.reason || '') + '</small></td>' +
        '<td class="num">' + esc(g.customer_cents) + '</td>' +
        '<td class="num">' + (held ? '-' : esc(g.old_seconds) + 's / ' + esc(g.old_minutes) + 'min / ' + esc(g.old_cents)) + '</td>' +
        '<td class="num">' + (held ? '-' : esc(g.new_seconds) + 's / ' + esc(g.new_minutes) + 'min / ' + esc(g.new_cents)) + '</td>' +
        '<td>' + (held ? '暂缓' : (g.balanced ? '<span class="check-ok">平</span>' : '<span class="check-bad">差</span>')) + '</td>' +
      '</tr>';
    }).join('') : '<tr><td class="empty" colspan="7">暂无交接结算结果，先试算或签发</td></tr>';
    var held = (settlement && settlement.held) || groups.filter(function (g) { return g.held; });
    $('ho-held').innerHTML = held.length ? held.map(function (g) {
      return '<li><span>' + esc(g.group_key || g.label) + '</span><code>HELD · ' +
        esc((g.reasons || [g.reason]).join(',')) + '</code></li>';
    }).join('') : '<li class="empty">无暂缓通话</li>';
  }
  function renderPostings(data) {
    var rows = data.postings || [];
    if (!rows.length) { $('ho-postings').innerHTML = ''; return; }
    $('ho-postings').innerHTML = '<h3>入账分录 ' + esc(data.posting_count || rows.length) +
      ' 条' + (data.idempotent_hit ? '（重跑命中幂等，未重复入账）' : '') + '</h3>' +
      '<div class="table-wrap"><table><thead><tr><th>批次</th><th>区间</th><th>运营商</th>' +
      '<th>秒</th><th>分</th><th>金额(分)</th></tr></thead><tbody>' +
      rows.map(function (p) {
        return '<tr><td><small>' + esc(p.batch_key) + '</small></td><td>' + esc(p.group_key) +
          '</td><td>' + esc(p.carrier) + '</td><td class="num">' + esc(p.seconds) +
          '</td><td class="num">' + esc(p.minutes) + '</td><td class="num">' + esc(p.amount_cents) + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }
  async function preview() {
    try {
      setStatus('试算中…');
      var data = await request('/api/handover/settle', { method: 'POST', body: JSON.stringify(buildBody({ preview: true }) ) });
      var s = data.settlement || data;
      renderRecon(s); renderGroups(s); $('ho-postings').innerHTML = '';
      setStatus('试算完成（未入账）');
    } catch (e) { setStatus(e.message, true); }
  }
  async function issue() {
    try {
      setStatus('签发入账中…');
      var data = await request('/api/handover/settle', { method: 'POST', body: JSON.stringify(buildBody({}) ) });
      var s = data.settlement || data;
      renderRecon(data.reconciliation ? data : s); renderGroups(s); renderPostings(data);
      setStatus((data.idempotent_hit ? '本批已入账，重跑未重复记账 · ' : '签发并入账 · ') +
        data.posting_count + ' 条分录 · batch ' + data.batch_key);
      if ($('ho-run-id') && data.run_id) $('ho-run-id').value = data.run_id;
    } catch (e) { setStatus(e.message, true); }
  }
  async function queryRun() {
    var runId = $('ho-run-id').value.trim();
    if (!runId) { setStatus('请填写 run_id', true); return; }
    try {
      setStatus('查询中…');
      var data = await request('/api/runs/' + encodeURIComponent(runId) + '/settlements');
      if (data.handover) { renderRecon(data.handover); renderGroups(data.handover); }
      renderPostings(data);
      setStatus('run ' + runId + '：' + data.posting_count + ' 条分录，合计 ' +
        (data.totals && data.totals.amount_total_cents) + ' 分');
    } catch (e) { setStatus(e.message, true); }
  }
  function boot() {
    if (!$('panel-handover')) return;
    $('ho-preview').addEventListener('click', preview);
    $('ho-issue').addEventListener('click', issue);
    $('ho-query-run').addEventListener('click', queryRun);
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
