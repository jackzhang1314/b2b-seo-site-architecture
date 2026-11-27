/* Both workbench views read the same project catalogue, including when opened from Finder. */
const architectureVersion = (() => {
  const context = window.architectureContext;
  const labels = {adopted:'当前采用', draft:'草案', previous:'历史采用'};
  let selected = null;
  const make = (tag, text, cls) => {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (cls) element.className = cls;
    return element;
  };

  function pathTo(revision) {
    const design = document.body.dataset.workbench === 'design';
    const file = design ? revision.href.replace(/index\.html$/, 'design.html') : revision.href;
    return context.root + file + location.hash;
  }
  function render() {
    const panel = document.getElementById('version-panel');
    const versions = window.architectureVersions || [];
    selected = versions.find(v => v.directory === context?.directory);
    const current = versions.find(v => v.role === 'adopted');
    const role = selected?.role;
    panel.dataset.role = role || 'snapshot';
    panel.replaceChildren();
    const summary = make('summary');
    summary.append(make('span', role ? labels[role] : '独立快照', 'version-badge'),
      make('strong', selected?.name || '工作台版本'));
    if(selected){
      const count=versions.filter(v=>v.role==='draft').length;
      const caption=role==='adopted'?(count?'另有 '+count+' 份草案':''):(current?'采用版 · '+current.name:'尚未采用方案');
      if(caption)summary.append(make('small',caption,'version-compare'));
    }
    summary.append(make('span', role ? '查看 / 切换版本' : '查看版本说明', 'version-action'));
    panel.append(summary);
    const content = make('div', '', 'version-content');
    panel.append(content);
    if (!selected) {
      content.append(make('p','请从网站项目根目录打开已登记的工作台，以核对当前采用版。此快照也可用完整目录单独交接。'));
    } else {
      content.append(make('p',current ? '项目默认读取「'+current.name+'」。新草案保留独立的页面规划和交接资料。' :
        '这个市场尚未采用方案。可以审阅草案，选定后再供日常 SEO 和建站使用。'));
      const list = make('ul', '', 'version-list');
      for (const revision of versions) {
        const row = make('li');
        const link = make('a',revision.name);
        link.href = pathTo(revision);
        for(const event of ['focus','pointerdown','click'])link.addEventListener(event,()=>{link.href=pathTo(revision);});
        if (revision === selected) link.setAttribute('aria-current','page');
        row.append(make('span',labels[revision.role],'version-role'), link);
        if (revision === selected) row.append(make('small','正在查看'));
        list.append(row);
      }
      content.append(list);
      if (role !== 'adopted' && current) {
        const changes = make('details');
        changes.append(make('summary','与当前采用版的页面差异 · '+selected.changes.length+' 页'));
        const wording = {added:'新增',removed:'移除',changed:'调整'};
        for (const change of selected.changes) changes.append(make('p',wording[change.action]+' · '+change.id+' · '+change.name));
        if (!selected.changes.length) changes.append(make('p','所比较的页面、模块和分词归属相同。'));
        content.append(changes);
        if(selected.opportunity_changes){
          const decisions=make('details');
          const states={pending:'待企业确认',research:'待补研究',page:'建独立页',section:'同页章节',filter:'筛选项',deferred:'暂缓',excluded:'不做'};
          decisions.append(make('summary','与当前采用版的机会差异 · '+selected.opportunity_changes.length+' 项'));
          for(const item of selected.opportunity_changes){
            const action=item.before===item.after?'依据或范围有调整':!item.before?'新增 · '+states[item.after]:
              !item.after?'移除':states[item.before]+' → '+states[item.after];
            decisions.append(make('p',item.name+' · '+action));
          }
          if(!selected.opportunity_changes.length)decisions.append(make('p','两版的机会及取舍记录相同。'));
          content.append(decisions);
        }
      }
      if (role !== 'adopted') {
        const help = make('details');
        help.append(make('summary','怎样采用这份方案'),make('p','确认后，在本网站项目的 Codex 聊天中发送以下指令。'));
        const task=make('textarea');
        task.readOnly=true;
        task.setAttribute('aria-label','采用此版本的指令');
        task.value='请把项目内「'+selected.directory+'」的架构工作台登记为当前采用版，保留原方案并读回项目入口。';
        help.append(task);content.append(help);
      }
    }
    document.dispatchEvent(new Event('architecture-version-ready'));
  }
  let loading=false;
  function refresh() {
    if(!context?.catalogue||loading)return;
    loading=true;
    const source=document.createElement('script');
    source.src=context.catalogue+'?read='+Date.now();
    const finish=() => {loading=false;source.remove();render();};
    source.onload=finish;
    source.onerror=() => {window.architectureVersions=[];finish();};
    document.head.append(source);
  }
  document.addEventListener('DOMContentLoaded',() => {
    render();refresh();
    window.addEventListener('pageshow',event=>{if(event.persisted)refresh();});
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  });
  return {
    get directory() { return selected?.directory; },
    get role() { return selected?.role; },
    get label() { return labels[selected?.role] || '独立快照'; }
  };
})();
