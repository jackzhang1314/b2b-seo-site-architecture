/* The project handoff file is authoritative. Browsers share it through the workbench editor. */
const pageChoices = (() => {
  const labels={build:'要建',skip:'不建',pending:'待定'};
  const expected=document.querySelector('meta[name="workbench-revision"]').content;
  const endpoint=new URL('workbench-handoff.json', location.href);
  const editing=new Set();
  let saved=null, pages=new Map(), writable=false, message='', readNumber=0;
  const make=(tag,text='',cls='')=>{
    const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;return el;
  };
  const signal=()=>document.dispatchEvent(new Event('workbench-choice-update'));
  const canonicalUrl=()=>location.protocol==='file:' && window.workbenchEditorUrl
    ? window.workbenchEditorUrl : new URL('index.html',location.href).href;
  function accept(context) {
    if(context.schema!=='b2b-seo-workbench-handoff/1'||context.revision_id!==expected||!Array.isArray(context.pages)){
      throw new Error('这份确认清单与当前方案不匹配，请重新打开工作台。');
    }
    if(saved && context.change_number<saved.change_number)return;
    saved=context;pages=new Map(context.pages.map(p=>[p.id,p]));
  }
  async function refresh() {
    if(location.protocol==='file:')return;
    const serial=++readNumber;
    const before=saved?.change_number, wasWritable=writable, previousMessage=message;
    try{
      const response=await fetch(endpoint,{cache:'no-store',headers:{Accept:'application/json'}});
      if(!response.ok)throw new Error('读取页面确认清单失败');
      const value=await response.json();
      if(serial!==readNumber)return;
      accept(value);
      if(message==='连接中断，可刷新页面重试。')message='';
      writable=response.headers.get('X-Workbench-Editor')==='1';
    }catch{
      if(serial!==readNumber)return;
      writable=false;message='连接中断，可刷新页面重试。';
    }
    if(before!==saved?.change_number||wasWritable!==writable||previousMessage!==message)signal();
  }
  async function choose(id,decision) {
    const page=pages.get(id);
    if(!writable||!page||editing.has(id)||page.decision===decision)return;
    editing.add(id);message='正在保存…';signal();
    try{
      const response=await fetch(endpoint,{method:'POST',cache:'no-store',
        headers:{'Content-Type':'application/json',Accept:'application/json'},
        body:JSON.stringify({revision_id:expected,page_id:id,expected_version:page.decision_version,decision})});
      const value=await response.json();
      if(!response.ok)throw new Error(value.error||'暂时无法保存。');
      accept(value);message='已保存到项目';
    }catch(err){
      await refresh();
      message=err.message==='Failed to fetch'?'未收到保存回执，请核对当前选择后重试。':err.message;
    }finally{
      editing.delete(id);signal();
      const active=document.activeElement;
      if(!active||active===document.body){
        const control=[...document.querySelectorAll('[data-choice-page] button')].find(b=>
          b.parentElement.dataset.choicePage===id && b.dataset.decision===decision);
        (control||document.getElementById('decision-filter'))?.focus({preventScroll:true});
      }
    }
  }
  function controls(id) {
    const el=make('div','','page-choice');
    const p=pages.get(id);
    if(!p){el.append(make('span','尚未载入选择','muted'));return el;}
    el.dataset.choicePage=id;el.setAttribute('role','group');el.setAttribute('aria-label',(p.name_zh||p.name)+' · 本次是否建页');
    if(!writable){el.append(make('strong',labels[p.decision],'choice-readonly'));return el;}
    for(const [decision,label] of Object.entries(labels)){
      const b=make('button',label);
      b.type='button';b.dataset.decision=decision;b.setAttribute('aria-pressed',String(p.decision===decision));
      b.disabled=editing.has(id);b.addEventListener('click',()=>choose(id,decision));el.append(b);
    }
    return el;
  }
  function note(id){
    const page=pages.get(id);
    return '工作台 '+canonicalUrl()+'。本页用户选择为'+(page?'「'+labels[page.decision]+'」':'待回读')+
      '。执行前读取同一工作台的最新确认清单。要建页按规划建站或更新内容，不建页和待定页只作范围参考。';
  }
  function summary(){
    const box=document.getElementById('selection-summary');
    const live=document.getElementById('brief-save-status');
    if(live)live.textContent=message||(writable?'选择自动保存到项目':'只读预览');
    if(!box)return;
    const counts={build:0,skip:0,pending:0};
    for(const p of pages.values())counts[p.decision]++;
    for(const k of Object.keys(counts))document.getElementById('choice-count-'+k).textContent=saved?counts[k]:'—';
    const prompt=document.getElementById('choice-status');
    prompt.textContent=editing.size?'正在保存…':message||(writable?'选择自动保存到项目':saved?'只读预览':'读取已保存的选择');
    document.getElementById('copy-workbench').disabled=editing.size>0;
    const open=document.getElementById('editable-workbench');
    open.hidden=writable||!window.workbenchEditorUrl||location.href===window.workbenchEditorUrl;
    if(!open.hidden)open.href=window.workbenchEditorUrl;
    document.getElementById('load-choice-file').hidden=location.protocol!=='file:';
    document.getElementById('readonly-help').hidden=writable||!saved&&location.protocol!=='file:';
  }
  document.addEventListener('DOMContentLoaded',()=>{
    const initial=document.getElementById('saved-page-context').textContent.trim();
    if(initial)accept(JSON.parse(initial));
    const btn=document.getElementById('copy-workbench');
    btn?.addEventListener('click',async()=>{
      const url=canonicalUrl(),fallback=document.getElementById('copy-link-text');
      try{await navigator.clipboard.writeText(url);fallback.hidden=true;message='已复制工作台链接';}
      catch{fallback.hidden=false;fallback.value=url;fallback.focus();fallback.select();message='请复制已选中的链接';}
      summary();
    });
    document.getElementById('choice-file')?.addEventListener('change',async event=>{
      const file=event.target.files[0];if(!file)return;
      try{accept(JSON.parse(await file.text()));message='已读取项目保存的清单';}
      catch{message='请选择这个版本的 workbench-handoff.json';}
      signal();
    });
    signal();refresh();
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
    window.addEventListener('pageshow',e=>{if(e.persisted)refresh();});
  });
  return {controls,note,summary,decision:id=>pages.get(id)?.decision,known:()=>Boolean(saved)};
})();
