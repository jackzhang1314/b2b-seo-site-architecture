/* Both views read choices and execution receipts from the same revision's handoff. */
const pageChoices = (() => {
  const choices = {
    build: {field:'decision', version:'decision_version', title:'建页选择', labels:{build:'要建', skip:'不建', pending:'待定'}},
    seo: {field:'seo_decision', version:'seo_version', title:'SEO 排期', labels:{requested:'安排优化', hold:'暂不安排'}}
  };
  const progressLabels = {
    build: {not_started:'暂无建页记录', built:'已建页', published:'建页已发布'},
    onpage: {not_started:'暂无优化记录', optimized:'已优化', published:'优化已发布', reviewed:'已核对现有内容'}
  };
  const alignmentLabels = {aligned:'本版已核对', needs_review:'本版 SEO 待复核'};
  const resolutionLabels = {pending:'待确定去向', merge:'并入其他页章节', defer:'本轮暂缓', out_of_scope:'业务不做'};
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
    if (page.seo_eligible === true) {
      if (page.business_question && !page.business_answer) return '可起草，业务资料待核';
      if (page.onpage_alignment === 'needs_review') return '先核对本版变化';
      return '可开始优化';
    }
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
    const mode = !editable ? 'read' : track === 'build' && current === 'pending' ? 'confirm' : 'select';
    if (buttons.dataset.mode !== mode) {
      buttons.dataset.mode = mode;
      buttons.dataset.editable = String(editable);
      buttons.replaceChildren();
      if (mode === 'confirm') {
        for (const decision of ['build', 'skip']) {
          const button = make('button', config.labels[decision]);
          button.type = 'button';
          button.dataset.decision = decision;
          button.addEventListener('click', () => choose(id, track, decision));
          buttons.append(button);
        }
      } else if (mode === 'select') {
        const select = make('select', '', 'choice-select');
        select.setAttribute('aria-label', '更改' + config.title + ' · ' + (page.name_zh || page.name || id));
        for (const [decision, caption] of Object.entries(config.labels)) select.add(new Option(caption, decision));
        select.addEventListener('change', () => choose(id, track, select.value));
        buttons.append(select);
      }
    }
    if (mode === 'select') {
      buttons.firstElementChild.value = current;
      buttons.firstElementChild.dataset.selected = current;
    }
    if (editable) {
      for (const control of buttons.children) control.disabled = offline || editing.has(editKey(id, track));
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
    const [label, alignment, when, open] = box.children;
    label.dataset.status = status;
    label.textContent = progressLabels[track][status] || (page ? '记录状态待核' : '尚未读取记录');
    const parts = [];
    if (active && receipt?.occurred_at) parts.push(formatTime(receipt.occurred_at, true));
    if (active) {
      const origin = receipt?.origin_revision_id || page.history?.find(event => event.track === track &&
        event.status === status && event.occurred_at === receipt?.occurred_at)?.origin_revision_id;
      if (origin && origin !== expected) {
        label.textContent = '前版' + (progressLabels[track][status] || status);
        parts.push('历史版本回执');
      }
      if (receipt?.last_published_at && status !== 'published') parts.push('上次发布 ' + formatTime(receipt.last_published_at, true));
    }
    when.textContent = parts.join(' · ');
    when.hidden = !parts.length;
    const state = track === 'onpage' ? page?.onpage_alignment : '';
    alignment.textContent = alignmentLabels[state] || '';
    alignment.dataset.alignment = state || '';
    alignment.hidden = !alignment.textContent;
    if (open) open.hidden = !active;
  }
  function progress(id, track, inspect) {
    const box = make('div', '', 'work-progress');
    box.dataset.progressPage = id;
    box.dataset.progressTrack = track;
    box.append(make('span', '', 'progress-label'), make('span', '', 'alignment-label'), make('small'));
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
    const changes = review.keyword_changes;
    if (changes?.added_count || changes?.removed_count) {
      const parts = [];
      for (const [direction, caption] of [['added', '新增'], ['removed', '移出']]) {
        const count = changes[direction + '_count'];
        if (!count) continue;
        const examples = changes[direction + '_examples'] || [];
        parts.push(caption + ' ' + count + ' 个词' + (examples.length ? ' · ' + examples.join('、') : ''));
      }
      box.append(make('small', parts.join('；')));
    }
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

  function unresolvedDemand(page) {
    if (page?.decision !== 'skip' || !page.assigned_keyword_ids?.length) return 0;
    const resolution = page.demand_resolution;
    if (!resolution || !resolutionLabels[resolution.action] || resolution.action === 'pending')
      return page.assigned_keyword_ids.length;
    if (resolution.action === 'merge') {
      const target = pages.get(resolution.target_page_id);
      if (!target || target.decision === 'skip' || !target.module_ids?.includes(resolution.target_module_id))
        return page.assigned_keyword_ids.length;
    }
    const reviewed = new Set(resolution.keyword_ids);
    return page.assigned_keyword_ids.reduce((count, id) => count + Number(!reviewed.has(id)), 0);
  }
  function matchesQueue(id, queue) {
    if (!queue) return true;
    const page = pages.get(id);
    if (!page) return false;
    if (queue === 'confirm') return page.decision === 'pending' || unresolvedDemand(page) > 0 ||
      Boolean(page.decision !== 'skip' && page.business_question && !page.business_answer);
    if (queue === 'build') return page.decision === 'build' && value(id, 'build') === 'not_started';
    return queue === 'review' && page.onpage_alignment === 'needs_review';
  }
  const inputPanels = new WeakMap();
  function field(label, control) {
    const box = make('label', label);
    box.append(control);
    return box;
  }
  function demandFields(row, modules) {
    const action = make('select'), target = make('select'), section = make('select'), reason = make('textarea');
    const saved = row.demand_resolution || {};
    for (const [key, text] of Object.entries(resolutionLabels)) action.add(new Option(text, key));
    action.value = saved.action || 'pending';
    target.add(new Option('选择页面', ''));
    for (const p of pages.values()) {
      if (p.id !== row.id && p.decision !== 'skip' && p.module_ids?.length)
        target.add(new Option(p.name_zh || p.name || p.id, p.id));
    }
    target.value = saved.target_page_id || '';
    reason.value = saved.reason || '';
    reason.rows = 2;
    const targetLabel = field('承接页面', target), sectionLabel = field('承接章节', section);
    function updateSections() {
      section.replaceChildren(new Option('选择章节', ''));
      const page = pages.get(target.value);
      for (const id of page?.module_ids || []) section.add(new Option(modules.get(page.id)?.get(id) || id, id));
      if (section.options.length === 2) section.selectedIndex = 1;
    }
    function updateMode() {
      const merge = action.value === 'merge';
      targetLabel.hidden = sectionLabel.hidden = !merge;
      target.required = section.required = merge;
    }
    target.addEventListener('change', updateSections);
    action.addEventListener('change', updateMode);
    updateSections();
    if (saved.target_module_id && [...section.options].some(o => o.value === saved.target_module_id)) section.value = saved.target_module_id;
    updateMode();
    return {fields:[field('需求去向', action), targetLabel, sectionLabel, field('原因（选填）', reason)],
      read:() => ({action:action.value, target_page_id:action.value === 'merge' ? target.value : '',
        target_module_id:action.value === 'merge' ? section.value : '', reason:reason.value.trim()})};
  }
  function openInput(box) {
    const state = inputPanels.get(box), row = pages.get(state.id);
    if (!row || state.form || offline || !writable) return;
    const form = make('form', '', 'inline-form'), business = state.track === 'business';
    let read;
    if (business) {
      const answer = make('textarea');
      answer.value = row.business_answer || '';
      answer.rows = 3;
      form.append(field('企业答复', answer));
      read = () => answer.value.trim();
    } else {
      form.append(make('small', '应用到本页当前分配的 ' + row.assigned_keyword_ids.length + ' 个采购词。'));
      const fields = demandFields(row, state.modules);
      form.append(...fields.fields);
      read = fields.read;
    }
    const bar = make('div', '', 'inline-form-actions');
    const save = make('button', '保存到项目', 'primary'), cancel = make('button', '取消');
    const retry = make('button', '已核对最新记录，保留我的输入'), result = make('small', '', 'inline-result');
    save.type = 'submit'; cancel.type = retry.type = 'button';
    retry.hidden = true;
    result.setAttribute('role', 'status');
    bar.append(save, cancel);
    form.append(result, retry, bar);
    state.form = form;
    state.baseVersion = row[state.version];
    state.retry = retry; state.result = result; state.save = save; state.cancel = cancel;
    box.append(form);
    state.open.hidden = true;
    const close = () => {
      form.remove();
      state.form = null;
      syncInput(box);
      state.open.focus({preventScroll:true});
      signal();
    };
    retry.addEventListener('click', () => {
      state.baseVersion = pages.get(state.id)?.[state.version];
      result.textContent = '';
      syncInput(box);
    });
    cancel.addEventListener('click', close);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (save.disabled) return;
      const ok = await saveValue(state.id, state.track, state.field, state.baseVersion, read());
      if (ok) close();
      else result.textContent = message;
    });
    syncInput(box);
    form.querySelector('textarea,select')?.focus({preventScroll:true});
  }
  function syncInput(box) {
    const state = inputPanels.get(box), row = pages.get(state.id);
    const business = state.track === 'business', resolution = row?.demand_resolution || {};
    const applies = business ? Boolean(row?.business_question || row?.business_answer)
      : row?.decision === 'skip' && row.assigned_keyword_ids?.length > 0;
    // A background refresh can change another page while the user is typing. Keep the open draft and its original version.
    box.hidden = !applies && !state.form;
    if (box.hidden) return;
    const left = business ? 0 : unresolvedDemand(row);
    if (business) {
      state.text.textContent = row?.business_question || '企业补充';
      state.saved.textContent = row?.business_answer ? '企业答复 · ' + row.business_answer : '';
      state.open.textContent = row?.business_answer ? '更改答复' : '填写答复';
    } else {
      const target = pages.get(resolution.target_page_id);
      const section = state.modules.get(target?.id)?.get(resolution.target_module_id) || resolution.target_module_id;
      const destination = resolution.action === 'merge' ? '并入 ' + (target?.name_zh || target?.name || resolution.target_page_id) +
        (section ? ' / ' + section : '') : resolutionLabels[resolution.action];
      state.text.textContent = left ? left + ' 个采购词待确定去向' : '采购需求 · ' + (destination || '待确定去向');
      const context = [left && destination && resolution.action !== 'pending' ? '已保存去向 · ' + destination : '', resolution.reason];
      state.saved.textContent = context.filter(Boolean).join(' · ');
      state.open.textContent = left ? '确定去向' : '更改去向';
    }
    state.saved.hidden = !state.saved.textContent;
    state.open.hidden = Boolean(state.form) || !writable || !Number.isInteger(row?.[state.version]);
    state.open.disabled = offline;
    if (!state.form) return;
    const busy = editing.has(editKey(state.id, state.track)), stale = row?.[state.version] !== state.baseVersion;
    state.retry.hidden = !applies || !stale;
    if (stale) state.result.textContent = '项目已有新记录。请核对上方的当前内容，再决定是否保存输入。';
    if (!applies) state.result.textContent = '页面状态已改变，请核对最新选择。当前输入仍可复制。';
    state.save.disabled = !applies || stale || busy || offline || !writable;
    state.cancel.disabled = busy;
    state.retry.disabled = busy || offline || !writable;
  }
  function inputs(id, modules) {
    const group = make('div', '', 'planning-inputs');
    for (const [track, name] of [['business','business_answer'], ['demand','demand_resolution']]) {
      const box = make('div', '', 'planning-input'), text = make('span'), saved = make('small'), open = make('button', '', 'linkbtn');
      box.dataset.inputPage = id; box.dataset.inputTrack = track;
      open.type = 'button'; open.addEventListener('click', () => openInput(box));
      inputPanels.set(box, {id, track, field:name, version:track + '_version', modules, text, saved, open});
      box.append(text, saved, open); group.append(box); syncInput(box);
    }
    return group;
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
    for (const track of ['build', 'onpage']) {
      counts[track].ever_published = [...pages.values()].filter(page => page.progress?.[track]?.last_published_at ||
        page.progress?.[track]?.status === 'published' || page.history?.some(event => event.track === track && event.status === 'published')).length;
    }
    for (const el of box.querySelectorAll('[data-queue-count]')) {
      const queue = el.dataset.queueCount;
      el.textContent = saved ? [...pages.keys()].filter(id => matchesQueue(id, queue)).length : '—';
    }
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
    document.querySelectorAll('[data-input-page]').forEach(syncInput);
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
  async function saveValue(id, track, field, version, next) {
    const key = editKey(id, track);
    if (!writable || offline || !Number.isInteger(version) || editing.has(key)) return false;
    editing.add(key);
    message = '';
    signal();
    try {
      const response = await fetch(endpoint, {method:'POST', cache:'no-store',
        headers:{'Content-Type':'application/json', Accept:'application/json'},
        body:JSON.stringify({revision_id:expected, page_id:id, expected_version:version, [field]:next})});
      const context = await response.json();
      if (!response.ok) throw new Error(context.error || '暂时无法保存。');
      if (accept(context)) {
        etag = response.headers.get('ETag') || etag;
        modified = response.headers.get('Last-Modified') || modified;
      }
      offline = false;
      message = '已保存到项目';
      return true;
    } catch (err) {
      // A read that began before the conflict may have an older view of this page.
      if (reading) await reading;
      await refresh();
      message = err.message === 'Failed to fetch' ? '未收到保存回执，请核对当前选择后重试。' : err.message;
      return false;
    } finally {
      editing.delete(key);
      signal();
    }
  }
  async function choose(id, track, decision) {
    const page = pages.get(id), config = choices[track];
    if (!page || page[config.field] === decision) return;
    await saveValue(id, track, config.field, page[config.version], decision);
    if (document.activeElement === document.body) {
      const control = [...document.querySelectorAll('[data-choice-page]')].find(el => el.dataset.choicePage === id && el.dataset.choiceTrack === track);
      (control?.querySelector('select,button') || document.querySelector('[data-work-queue][aria-pressed=true]'))?.focus({preventScroll:true});
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
  return {controls, progress, pagePanel, reviewNotice, inputs, matchesQueue, value, note, summary, decision:id => value(id, 'decision'), known:() => Boolean(saved)};
})();
