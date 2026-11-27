/* Both views read choices and execution receipts from the same revision's handoff. */
const pageChoices = (() => {
  const choices = {
    build: {field:'decision', version:'decision_version', title:'建页选择', labels:{build:'要建', skip:'不建', pending:'待定'}},
    seo: {field:'seo_decision', version:'seo_version', title:'SEO 排期', labels:{requested:'安排优化', hold:'暂不安排'}}
  };
  const progressLabels = {
    build: {not_started:'暂无建页记录', built:'已建页', published:'建页已发布'},
    onpage: {not_started:'暂无优化记录', optimized:'已优化', published:'优化已发布'}
  };
  const reviewLabels = {new:'本版新增', changed:'本版规划有更新', moved:'页面已迁移，待复核', conflict:'历史页面归属待核'};
  const expected = document.querySelector('meta[name="workbench-revision"]').content;
  const endpoint = new URL('workbench-handoff.json', location.href);
  const editing = new Set();
  const historySignatures = new WeakMap();
  const clock = new Intl.DateTimeFormat('zh-CN', {dateStyle:'medium', timeStyle:'long'});
  const calendar = new Intl.DateTimeFormat('zh-CN', {year:'numeric', month:'numeric', day:'numeric'});
  let saved = null, pages = new Map(), writable = false, message = '', offline = false;
  let reading = null, etag = '', modified = '';
  const make = (tag, text='', cls='') => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };
  const canonicalUrl = () => location.protocol === 'file:' && window.workbenchEditorUrl
    ? window.workbenchEditorUrl : new URL('index.html', location.href).href;
  const editKey = (id, track) => track + '/' + id;

  function accept(context) {
    if (context?.schema !== 'b2b-seo-workbench-handoff/1' || context.revision_id !== expected ||
        !Array.isArray(context.pages) || !context.pages.every(p => p && typeof p.id === 'string')) {
      throw new Error('此清单与当前版本不匹配，请重新打开工作台。');
    }
    if (saved && context.change_number < saved.change_number) return false;
    saved = context;
    pages = new Map(context.pages.map(p => [p.id, p]));
    return true;
  }
  function value(id, field) {
    const page = pages.get(id);
    if (!page) return '';
    if (field === 'build' || field === 'onpage') return page.progress?.[field]?.status || 'not_started';
    return page[field] ?? '';
  }
  function scheduleNote(page) {
    if (page?.seo_decision !== 'requested') return '';
    if (page.seo_eligible === true) return '可执行';
    if (page.seo_eligible === false) return page.decision === 'skip' ? '本次不建，SEO 等待建页确认' : '等待建页确认';
    return '执行资格待核';
  }
  function formatTime(value, brief=false) {
    if (!value) return '';
    const stamp = new Date(value);
    return Number.isNaN(stamp.getTime()) ? String(value) : (brief ? calendar : clock).format(stamp);
  }

  function syncChoice(box) {
    const id = box.dataset.choicePage, track = box.dataset.choiceTrack, config = choices[track];
    const page = pages.get(id), current = page?.[config.field], label = config.labels[current];
    const editable = Boolean(writable && label && Number.isInteger(page[config.version]));
    box.setAttribute('aria-label', (page?.name_zh || page?.name || id) + ' · ' + config.title);
    const buttons = box.firstElementChild, note = box.lastElementChild;
    if (buttons.dataset.editable !== String(editable)) {
      buttons.dataset.editable = String(editable);
      buttons.replaceChildren();
      if (editable) for (const [decision, caption] of Object.entries(config.labels)) {
        const button = make('button', caption);
        button.type = 'button';
        button.dataset[track === 'build' ? 'decision' : 'seoDecision'] = decision;
        button.addEventListener('click', () => choose(id, track, decision));
        buttons.append(button);
      }
    }
    if (editable) {
      for (const button of buttons.children) {
        button.setAttribute('aria-pressed', String((button.dataset.decision || button.dataset.seoDecision) === current));
        button.disabled = offline || editing.has(editKey(id, track));
      }
    } else {
      buttons.textContent = label || (!page ? '尚未读取' : track === 'seo' ? '旧版未记录排期' : '选择待核');
    }
    note.textContent = track === 'seo' ? scheduleNote(page) : '';
    note.hidden = !note.textContent;
  }
  function controls(id, track='build') {
    const box = make('div', '', 'page-choice');
    box.dataset.choicePage = id;
    box.dataset.choiceTrack = track;
    box.setAttribute('role', 'group');
    box.append(make('div', '', 'choice-buttons'), make('small', '', 'schedule-note'));
    syncChoice(box);
    return box;
  }

  function syncProgress(box) {
    const id = box.dataset.progressPage, track = box.dataset.progressTrack;
    const page = pages.get(id), receipt = page?.progress?.[track];
    const status = value(id, track), active = ['built','optimized','published'].includes(status);
    const [label, when, open] = box.children;
    label.dataset.status = status;
    label.textContent = progressLabels[track][status] || (page ? '记录状态待核' : '尚未读取记录');
    const parts = [];
    if (active && receipt?.occurred_at) parts.push(formatTime(receipt.occurred_at, true));
    if (active) {
      const origin = receipt?.origin_revision_id || page.history?.find(event => event.track === track &&
        event.status === status && event.occurred_at === receipt?.occurred_at)?.origin_revision_id;
      if (origin && origin !== expected) parts.push('历史版本回执');
      if (receipt?.last_published_at && status !== 'published') parts.push('上次发布 ' + formatTime(receipt.last_published_at, true));
    }
    when.textContent = parts.join(' · ');
    when.hidden = !parts.length;
    if (open) open.hidden = !active;
  }
  function progress(id, track, inspect) {
    const box = make('div', '', 'work-progress');
    box.dataset.progressPage = id;
    box.dataset.progressTrack = track;
    box.append(make('span', '', 'progress-label'), make('small'));
    if (inspect) {
      const open = make('button', '查看回执', 'linkbtn receipt-link');
      open.type = 'button';
      open.setAttribute('aria-label', (track === 'build' ? '查看建页回执' : '查看优化回执') + ' · ' + id);
      open.addEventListener('click', inspect);
      box.append(open);
    }
    syncProgress(box);
    return box;
  }
  function appendEvidence(item, evidence) {
    if (!evidence) return;
    const line = make('div', '', 'receipt-evidence');
    line.append(make('strong', '证据'));
    let target;
    try { target = new URL(evidence); } catch { /* Project paths and readback references are displayed as text. */ }
    if (target && ['http:', 'https:'].includes(target.protocol) && !target.username && !target.password) {
      const link = make('a', evidence);
      link.href = target.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      line.append(link);
    } else line.append(make('code', evidence));
    item.append(line);
  }
  function receipts(page) {
    const events = Array.isArray(page?.history) ? [...page.history].reverse() : [];
    for (const track of Object.keys(progressLabels)) {
      const latest = page?.progress?.[track];
      if (latest?.status && latest.status !== 'not_started' && !events.some(event => event.track === track)) {
        events.push({...latest, track});
      }
    }
    return events.sort((a, b) => (Date.parse(b.occurred_at) || 0) - (Date.parse(a.occurred_at) || 0));
  }
  function syncHistory(panel) {
    const page = pages.get(panel.dataset.historyPage), events = receipts(page);
    panel.firstElementChild.textContent = '执行回执' + (page ? '（' + events.length + '）' : '');
    if (!panel.open) return;
    const list = panel.lastElementChild, signature = JSON.stringify([Boolean(page), events]);
    if (historySignatures.get(panel) === signature) return;
    historySignatures.set(panel, signature);
    const focusedEvidence = list.contains(document.activeElement) ? document.activeElement.closest('li')?.dataset.eventId : '';
    list.replaceChildren();
    if (!events.length) list.append(make('li', page ? '暂无执行回执。' : '读取本版清单后可核对回执。', 'muted'));
    for (const event of events) {
      const item = make('li'), heading = make('div', '', 'receipt-heading');
      item.dataset.eventId = event.event_id || '';
      const track = event.track === 'build' ? '建页' : 'On-page SEO';
      heading.append(make('strong', track + ' · ' + (progressLabels[event.track]?.[event.status] || event.status)));
      if (event.occurred_at) {
        const time = make('time', formatTime(event.occurred_at));
        time.dateTime = event.occurred_at;
        heading.append(time);
      }
      if (event.origin_revision_id && event.origin_revision_id !== expected) heading.append(make('small', '历史版本回执'));
      item.append(heading);
      if (event.summary) item.append(make('p', event.summary));
      appendEvidence(item, event.evidence);
      if (event.event_id) item.append(make('small', '记录编号 ' + event.event_id, 'muted'));
      list.append(item);
      if (focusedEvidence && focusedEvidence === item.dataset.eventId) item.querySelector('a')?.focus({preventScroll:true});
    }
  }
  function historyPanel(id) {
    const panel = make('details', '', 'receipt-history');
    panel.dataset.historyPage = id;
    panel.append(make('summary'), make('ol'));
    panel.addEventListener('toggle', () => syncHistory(panel));
    syncHistory(panel);
    return panel;
  }
  function syncReview(box) {
    const review = pages.get(box.dataset.reviewPage)?.revision_review;
    const label = reviewLabels[review?.status];
    box.hidden = !label;
    if (!label) return;
    box.textContent = label;
    if (box.dataset.full === 'true') {
      const source = Array.isArray(review.source_pages) ? review.source_pages.map(p => [p.id, p.url].filter(Boolean).join(' · ')).filter(Boolean) : [];
      if (review.reason || review.summary) box.append(make('small', review.reason || review.summary));
      if (source.length) box.append(make('small', '来源页面 ' + source.join('，')));
    }
  }
  function reviewNotice(id, full=false) {
    const box = make('div', '', 'revision-review');
    box.dataset.reviewPage = id;
    box.dataset.full = String(full);
    syncReview(box);
    return box;
  }
  function pagePanel(id) {
    const panel = make('section', '', 'page-execution');
    panel.setAttribute('aria-label', '本页选择与执行记录');
    const grid = make('div', '', 'execution-grid');
    for (const [caption, body] of [
      ['建页选择', controls(id)], ['实际建页', progress(id, 'build')],
      ['SEO 排期', controls(id, 'seo')], ['实际优化', progress(id, 'onpage')]
    ]) {
      const block = make('div');
      block.append(make('h3', caption), body);
      grid.append(block);
    }
    panel.append(reviewNotice(id, true), grid, historyPanel(id));
    return panel;
  }

  function summary() {
    const status = editing.size ? '正在保存…' : offline ? '连接中断，将自动重试' :
      message || (writable ? '选择自动保存到项目' : saved ? '只读预览' : '尚未读取项目清单');
    const live = document.getElementById('brief-save-status');
    if (live) live.textContent = status;
    const box = document.getElementById('selection-summary');
    if (!box) return;
    const counts = {decision:{}, seo_decision:{}, build:{}, onpage:{}};
    for (const page of pages.values()) for (const [field, totals] of Object.entries(counts)) {
      const state = value(page.id, field);
      totals[state] = (totals[state] || 0) + 1;
    }
    counts.build.completed = (counts.build.built || 0) + (counts.build.published || 0);
    counts.onpage.completed = (counts.onpage.optimized || 0) + (counts.onpage.published || 0);
    for (const el of box.querySelectorAll('[data-tally]')) {
      const [field, state] = el.dataset.tally.split('.');
      const totals = counts[field];
      el.textContent = saved && totals[''] !== pages.size ? totals[state] || 0 : '—';
    }
    const missing = document.getElementById('unspecified-seo');
    const unknown = counts.seo_decision[''] || 0;
    missing.hidden = !unknown;
    if (unknown) missing.textContent = unknown + ' 页未记录排期';
    document.getElementById('choice-status').textContent = status;
    document.getElementById('copy-workbench').disabled = editing.size > 0;
    const open = document.getElementById('editable-workbench');
    open.hidden = writable || !window.workbenchEditorUrl || location.href === window.workbenchEditorUrl;
    if (!open.hidden) open.href = window.workbenchEditorUrl;
    document.getElementById('load-choice-file').hidden = location.protocol !== 'file:';
    document.getElementById('readonly-help').hidden = writable || (!saved && location.protocol !== 'file:');
  }
  function signal() {
    document.querySelectorAll('[data-choice-page]').forEach(syncChoice);
    document.querySelectorAll('[data-progress-page]').forEach(syncProgress);
    document.querySelectorAll('[data-history-page]').forEach(syncHistory);
    document.querySelectorAll('[data-review-page]').forEach(syncReview);
    summary();
    document.dispatchEvent(new Event('workbench-choice-update'));
  }
  async function readLatest() {
    const headers = {Accept:'application/json'};
    if (etag) headers['If-None-Match'] = etag;
    else if (modified) headers['If-Modified-Since'] = modified;
    const wasOffline = offline, wasWritable = writable;
    let updated = false;
    try {
      const response = await fetch(endpoint, {cache:'no-store', headers});
      if (response.status === 304) {
        if (response.headers.has('X-Workbench-Editor')) writable = response.headers.get('X-Workbench-Editor') === '1';
      } else {
        if (!response.ok) throw new Error('读取项目清单失败');
        updated = accept(await response.json());
        if (updated) {
          etag = response.headers.get('ETag') || '';
          modified = response.headers.get('Last-Modified') || '';
          writable = response.headers.get('X-Workbench-Editor') === '1';
        }
      }
      offline = false;
    } catch { offline = true; }
    if (updated || wasOffline !== offline || wasWritable !== writable) signal();
  }
  function refresh() {
    if (location.protocol === 'file:') return;
    if (!reading) reading = readLatest().finally(() => { reading = null; });
    return reading;
  }
  async function choose(id, track, decision) {
    const page = pages.get(id), config = choices[track], key = editKey(id, track);
    if (!writable || offline || !page || !Number.isInteger(page[config.version]) || editing.has(key) || page[config.field] === decision) return;
    editing.add(key);
    message = '';
    signal();
    try {
      const response = await fetch(endpoint, {method:'POST', cache:'no-store',
        headers:{'Content-Type':'application/json', Accept:'application/json'},
        body:JSON.stringify({revision_id:expected, page_id:id, expected_version:page[config.version], [config.field]:decision})});
      const context = await response.json();
      if (!response.ok) throw new Error(context.error || '暂时无法保存。');
      if (accept(context)) {
        etag = response.headers.get('ETag') || etag;
        modified = response.headers.get('Last-Modified') || modified;
      }
      offline = false;
      message = '已保存到项目';
    } catch (err) {
      // A read that began before the conflict may have an older view of this page.
      if (reading) await reading;
      await refresh();
      message = err.message === 'Failed to fetch' ? '未收到保存回执，请核对当前选择后重试。' : err.message;
    } finally {
      editing.delete(key);
      signal();
      if (document.activeElement === document.body) {
        const control = [...document.querySelectorAll('[data-choice-page]')].find(el => el.dataset.choicePage === id && el.dataset.choiceTrack === track);
        const button = control && [...control.querySelectorAll('button')].find(b => (b.dataset.decision || b.dataset.seoDecision) === decision);
        (button || document.getElementById(track === 'build' ? 'decision-filter' : 'seo-filter'))?.focus({preventScroll:true});
      }
    }
  }
  function note(id) {
    const page = pages.get(id);
    return '工作台 ' + canonicalUrl() + '，页面编号 ' + id + '。当前建页选择「' +
      (choices.build.labels[page?.decision] || '待回读') + '」，SEO 排期「' +
      (choices.seo.labels[page?.seo_decision] || '待回读') + '」。执行前回读同版清单。建页按 decision=build 执行，SEO 按 seo_decision=requested 且 seo_eligible=true 执行。';
  }
  document.addEventListener('DOMContentLoaded', () => {
    const initial = document.getElementById('saved-page-context').textContent.trim();
    if (initial) {
      try { accept(JSON.parse(initial)); } catch { message = '请重新读取本版项目清单'; }
    }
    document.getElementById('copy-workbench')?.addEventListener('click', async () => {
      const url = canonicalUrl(), fallback = document.getElementById('copy-link-text');
      try { await navigator.clipboard.writeText(url); fallback.hidden = true; message = '已复制工作台链接'; }
      catch { fallback.hidden = false; fallback.value = url; fallback.focus(); fallback.select(); message = '请复制已选中的链接'; }
      summary();
    });
    document.getElementById('choice-file')?.addEventListener('change', async event => {
      const file = event.target.files[0];
      if (!file) return;
      try { accept(JSON.parse(await file.text())); message = '已读取项目保存的清单'; }
      catch { message = '请选择这个版本的 workbench-handoff.json'; }
      signal();
    });
    signal();
    refresh();
    if (location.protocol !== 'file:') {
      // Current editors supply validators. Static servers without one refresh only on returning to the tab.
      window.setInterval(() => { if (!document.hidden && (etag || modified || offline) && !editing.size) refresh(); }, 20000);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
      window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
    }
  });
  return {controls, progress, pagePanel, reviewNotice, value, note, summary, decision:id => value(id, 'decision'), known:() => Boolean(saved)};
})();
