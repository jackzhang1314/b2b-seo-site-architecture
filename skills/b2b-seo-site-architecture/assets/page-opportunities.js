const opportunityLabels = {pending:'待企业确认',research:'待补研究',page:'建独立页',section:'同页章节',filter:'筛选项',deferred:'暂缓',excluded:'不做'};
const opportunityFilters = {all:'全部',pending:'待企业确认',research:'待补研究',resolved:'已作取舍'};
let opportunityFilter = 'all', opportunityId = '';
const opportunityState = row => ['pending','research'].includes(row.status) ? row.status : 'resolved';

function openOpportunities(filter, id='') {
  reset('opportunities');
  opportunityFilter=filter;opportunityId=id;navigate();
}

function opportunityTabs(parent) {
  const counts={all:data.opportunities.length,pending:0,research:0,resolved:0};
  for(const row of data.opportunities)counts[opportunityState(row)]++;
  const tabs=node('div','','op-filters');
  tabs.setAttribute('role','group');tabs.setAttribute('aria-label','按建页机会状态筛选');
  for(const [key,label] of Object.entries(opportunityFilters)){
    const b=action(label+' '+counts[key],()=>{
      opportunityFilter=key;opportunityId='';offset=0;navigate();
    });
    b.setAttribute('aria-pressed',String(opportunityFilter===key));tabs.append(b);
  }
  parent.append(tabs);
}

function showOpportunity(parent,row) {
  const card=node('details','','op-card');
  card.dataset.opportunity=row.id;
  const summary=node('summary'), badge=node('span',opportunityLabels[row.status],'op-status');
  badge.dataset.status=opportunityState(row);
  summary.append(badge,node('strong',row.title));card.append(summary);parent.append(card);
  const fill=()=>{
    if(card.childElementCount>1)return;
    const body=node('div');card.append(body);
    if(row.question)body.append(node('h3',row.question));
    line(body,'判断依据',row.reason);
    if(row.page_id)body.append(action('查看承接页 · '+pageName(pageById.get(row.page_id)),()=>openPage(row.page_id)));
    for(const url of row.urls){
      external(body,url);
      if(data.urls.some(u=>u.url===url&&u.market===row.market&&u.language===row.language)){
        body.append(action('核对该网址的流量与排名',()=>{
          reset('urls');selectedUrl=url;locale=JSON.stringify([row.market,row.language]);byId('locale').value=locale;navigate();
        }));
      }
    }
    if(row.traffic_refs.length){
      const refs=details('Top Pages 原始行 · '+row.traffic_refs.length,body);
      for(const ref of row.traffic_refs)sourceRef(refs,'rankings',ref);
    }
    if(row.keyword_ids.length){
      const members=details('关联查询 · '+row.keyword_ids.length,body);
      members.addEventListener('toggle',()=>{
        if(!members.open||members.childElementCount>1)return;
        for(const kid of row.keyword_ids)members.append(action(wordById.get(kid).keyword,()=>openWords({word:kid})));
      });
    }
    const facts=[...row.offering_ids.map(id=>data.opportunity_offers[id]||id),
      ...row.evidence_ids.map(id=>data.source_notes.find(s=>s.id===id)?.label||data.inputs.find(s=>s.source_id===id)?.label||id)];
    if(facts.length)line(body,'企业业务与证据',facts);

  };
  card.open=row.id===opportunityId;
  if(card.open)fill();
  card.addEventListener('toggle',()=>{
    if(!card.isConnected)return;
    if(card.open)fill();
    if(!card.open && opportunityId!==row.id)return;
    opportunityId=card.open?row.id:'';
    const hash=new URLSearchParams(location.hash.slice(1));hash.set('view','opportunities');
    if(opportunityId)hash.set('item',opportunityId);else hash.delete('item');
    history.replaceState(null,'','#'+hash);
  });
}
