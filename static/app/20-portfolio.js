/* -----------------------------------------------------------------------
   Cross-period job matching. Values are used only as hard consistency
   checks (costs and billings cannot go backwards), never as plausibility
   scores. IDs and names generate the candidate order.
   ----------------------------------------------------------------------- */
const{identityScore,nameSimilarity,normId,normName,plausibleCandidate}=WippleJobMatching;
const{deriveCanonicalVars,mappingReadiness,inferCorroboratingColumns}=WippleMath;
function tableIdentity(t,i){
  const row=(t?.rows||[])[i]||{};
  const id=(t?.job_ids||[])[i]??row.job_id??"";
  const name=(t?.job_names||[])[i]??row.job_name??"";
  const fallback=(t?.job_labels||[])[i]??row.label??"";
  const looksId=/^[A-Za-z0-9][A-Za-z0-9._/#-]*$/.test(fallback)&&/\d/.test(fallback);
  return {jobId:String(id||(!name&&looksId?fallback:"")),
    jobName:String(name||(!id&&!looksId?fallback:"")),
    label:String(name||id||fallback||`Row ${i+1}`)};
}
function canonicalVars(rep,i,accepted=null){
  const t=rep.table||{},out={};
  if(Array.isArray(t.values)){
    (t.columns||[]).forEach((c,j)=>{
      if(c.variable&&Number.isFinite(+t.values?.[i]?.[j]))out[c.variable]=+t.values[i][j];
    });
    const j=(rep.analysis?.jobs||[])[i]||{};
    for(const k of["V","C","D","E","B","U","O","P"])
      if(out[k]==null&&Number.isFinite(+j[k]))out[k]=+j[k];
  }else{
    const vals=(t.rows||[])[i]?.values||{};
    Object.entries(vals).forEach(([k,v])=>{if(Number.isFinite(+v))out[k]=+v;});
    if(out.RT!=null)out.V=out.RT;
    if(out.KT!=null){out.C=out.KT;out.D=out.KT;}
    if(out.RT!=null)out.E=out.RT;
    if(out.BC!=null)out.B=out.BC;
    if(out.GT!=null)out.G=out.GT;
  }
  const corrections=rep.analysis?.corrections||[];
  corrections.forEach((correction,ci)=>{
    if(correction.row===i&&correction.variable
      &&(!accepted||accepted.has(ci))&&Number.isFinite(+correction.implied))
      out[correction.variable]=+correction.implied;
  });
  return deriveCanonicalVars(out);
}
function batchObservations(){
  const out=[];
  for(const item of BATCH_ITEMS.filter(x=>x.status==="ready")){
    documentSections(item.doc).forEach((sec,si)=>{
      const t=sec.rep.table||{},n=tableJobCount(t);
      const type=item.scheduleType==="mixed"?sec.type:item.scheduleType;
      const corrections=sec.rep.analysis?.corrections||[];
      const accepted=item.reviewState?.states?.[si]
        ||new Set(corrections.map((_,ci)=>ci));
      for(let i=0;i<n;i++){
        const ident=tableIdentity(t,i);
        out.push({key:`${item.id}:${si}:${i}`,itemId:item.id,section:si,row:i,
          period:item.periodEnd,type,source:item.name,...ident,
          vars:canonicalVars(sec.rep,i,accepted),manual:false});
      }
    });
  }
  return out;
}
function chronologicalPair(a,b){
  return a.period<=b.period?[a,b]:[b,a];
}
function observationsCompatible(a,b){
  if(a.period===b.period)return true;
  const[old,newer]=chronologicalPair(a,b),tol=1;
  for(const k of["D","B"]){
    if(old.vars[k]!=null&&newer.vars[k]!=null&&newer.vars[k]+tol<old.vars[k])
      return false;
  }
  return true;
}
function observationScore(a,b){
  if(!observationsCompatible(a,b))return-1;
  return identityScore(a,b);
}
function groupScore(obs,g){return Math.max(...g.observations.map(x=>observationScore(obs,x)),-1)}
function crossPeriodGroupScore(obs,g){
  const earlier=g.observations.filter(x=>x.period!==obs.period);
  return Math.max(...earlier.map(x=>observationScore(obs,x)),-1);
}
function buildMatchState(){
  const observations=batchObservations().sort((a,b)=>
    a.period.localeCompare(b.period)||a.itemId-b.itemId||a.row-b.row);
  const groups=[],pending=[];
  for(const obs of observations){
    const ranked=groups.map(g=>({g,score:groupScore(obs,g)}))
      .filter(x=>x.score>=0).sort((a,b)=>b.score-a.score);
    const best=ranked[0],second=ranked[1];
    const auto=best&&best.score>=.9&&(!second||best.score-second.score>=.08);
    if(auto){
      best.g.observations.push(obs);obs.match="auto";
    }else{
      const g={id:`g${groups.length+1}`,observations:[obs]};
      groups.push(g);obs.match=groups.length===1||!groups.some(x=>
        x!==g&&x.observations.some(o=>o.period<obs.period))?"new":"pending";
      if(obs.match==="pending")pending.push(obs.key);
    }
  }
  APP_STATE.batch.matchState={observations,groups,pending,selected:0,decisions:[]};
  const reviewable=new Set(pending.filter(key=>{
    const obs=observations.find(o=>o.key===key);
    return obs&&matchingCandidates(obs).length;
  }));
  for(const key of pending){
    if(reviewable.has(key))continue;
    const obs=observations.find(o=>o.key===key);
    if(obs)obs.match="new";
  }
  APP_STATE.batch.matchState.pending=pending.filter(key=>reviewable.has(key));
  wireFlowNav();
}
function findGroupByObs(key){
  return APP_STATE.batch.matchState?.groups.find(g=>g.observations.some(o=>o.key===key));
}
function matchingCandidates(obs){
  return APP_STATE.batch.matchState.groups.filter(g=>!g.observations.some(o=>o.key===obs.key))
    .filter(g=>g.observations.some(o=>o.period!==obs.period))
    .map(g=>({g,score:crossPeriodGroupScore(obs,g)}))
    .filter(x=>x.score>=.60&&x.g.observations.some(o=>
      o.period!==obs.period&&plausibleCandidate(obs,o)))
    .sort((a,b)=>b.score-a.score);
}
function mergeObservation(obsKey,targetId,rerender=true){
  const from=findGroupByObs(obsKey),target=APP_STATE.batch.matchState.groups.find(g=>g.id===targetId);
  if(!from||!target||from===target)return;
  const obs=from.observations.find(o=>o.key===obsKey);
  const moved=from.observations.every(o=>o.period===obs.period)?[...from.observations]:[obs];
  from.observations=from.observations.filter(o=>!moved.includes(o));
  target.observations.push(...moved);
  moved.forEach(o=>{o.match="manual";o.manual=true;});
  if(!from.observations.length)APP_STATE.batch.matchState.groups=APP_STATE.batch.matchState.groups.filter(g=>g!==from);
  APP_STATE.batch.matchState.pending=APP_STATE.batch.matchState.pending.filter(k=>k!==obsKey);
  APP_STATE.batch.matchState.decisions=APP_STATE.batch.matchState.decisions.filter(d=>d.obsKey!==obsKey);
  APP_STATE.batch.matchState.decisions.push({obsKey,type:"matched",targetId,movedKeys:moved.map(o=>o.key)});
  APP_STATE.batch.matchState.selected=Math.min(APP_STATE.batch.matchState.selected,APP_STATE.batch.matchState.pending.length-1);
  if(rerender)renderMatching(true);
}
function markNewJob(obsKey,rerender=true){
  const obs=APP_STATE.batch.matchState.observations.find(o=>o.key===obsKey);
  if(obs){obs.match="new";obs.manual=true;}
  APP_STATE.batch.matchState.pending=APP_STATE.batch.matchState.pending.filter(k=>k!==obsKey);
  APP_STATE.batch.matchState.decisions=APP_STATE.batch.matchState.decisions.filter(d=>d.obsKey!==obsKey);
  APP_STATE.batch.matchState.decisions.push({obsKey,type:"new"});
  APP_STATE.batch.matchState.selected=Math.min(APP_STATE.batch.matchState.selected,APP_STATE.batch.matchState.pending.length-1);
  if(rerender)renderMatching(true);
}
function undoMatchDecision(obsKey,rerender=true){
  const decision=APP_STATE.batch.matchState.decisions.find(d=>d.obsKey===obsKey);
  const obs=APP_STATE.batch.matchState.observations.find(o=>o.key===obsKey);
  if(!decision||!obs)return;
  if(decision.type==="matched"){
    const group=findGroupByObs(obsKey);
    if(group&&group.observations.length>1){
      const keys=new Set(decision.movedKeys||[obsKey]);
      const moved=group.observations.filter(o=>keys.has(o.key));
      group.observations=group.observations.filter(o=>!keys.has(o.key));
      APP_STATE.batch.matchState.groups.push({id:`g${Date.now()}`,observations:moved});
    }
  }
  obs.match="pending";obs.manual=false;
  if(!APP_STATE.batch.matchState.pending.includes(obsKey))APP_STATE.batch.matchState.pending.push(obsKey);
  APP_STATE.batch.matchState.decisions=APP_STATE.batch.matchState.decisions.filter(d=>d.obsKey!==obsKey);
  if(rerender)renderMatching(true);
}
function unlinkPeriodFromGroup(groupId,period,rerender=true){
  const g=APP_STATE.batch.matchState.groups.find(x=>x.id===groupId);
  if(!g||new Set(g.observations.map(o=>o.period)).size<2)return null;
  const moved=g.observations.filter(o=>o.period===period);
  if(!moved.length)return null;
  g.observations=g.observations.filter(o=>o.period!==period);
  const ng={id:`g${Date.now()}`,observations:moved};APP_STATE.batch.matchState.groups.push(ng);
  moved.forEach(o=>o.match="new");
  const decision=[...moved].sort((a,b)=>(a.type==="cc"?1:0)-(b.type==="cc"?1:0)).at(-1);
  APP_STATE.batch.matchState.decisions=APP_STATE.batch.matchState.decisions.filter(d=>!moved.some(o=>o.key===d.obsKey));
  if(decision)APP_STATE.batch.matchState.decisions.push({obsKey:decision.key,type:"new",reason:"unlinked",
    movedKeys:moved.map(o=>o.key)});
  APP_STATE.batch.matchState.selected=APP_STATE.batch.matchState.pending.length-1;
  if(rerender)renderMatching(true);
  return decision?.key||null;
}
function obsTitle(o){return o.jobName||o.jobId||o.label}
function matchReason(a,b){
  const ia=normId(a.jobId),ib=normId(b.jobId);
  if(ia&&ib&&ia===ib)return"Same job ID";
  if(normName(a.jobName)&&normName(a.jobName)===normName(b.jobName))return"Same job name";
  return"Similar job name";
}
function obsFacts(o){
  const pairs=[["Contract",o.vars.V],["Total cost",o.vars.C],["Cost to date",o.vars.D],
    ["Earned",o.vars.E],["Billed",o.vars.B],["Gross profit",o.vars.G]]
    .filter(([,v])=>v!=null);
  return pairs.map(([k,v])=>`<span><em style="font-style:normal;color:var(--muted)">${k}</em><b style="font-weight:500">$${fmt$(v)}</b></span>`).join("");
}
function candidateHTML(obs,{g}){
  const ref=g.observations.filter(o=>o.period!==obs.period)
    .sort((a,b)=>b.period.localeCompare(a.period))[0];
  return`<div class="candidate"><div class="candidate-main"><div>
      <strong title="${htmlEsc(obsTitle(ref))}">${htmlEsc(obsTitle(ref))}</strong>
      <small>${htmlEsc(ref.jobId||"No job ID")} · ${matchReason(obs,ref)}</small>
      <div class="match-source">${displayPeriod(ref.period)} · ${ref.type==="cc"?"Completed contracts":"WIP"} · ${htmlEsc(ref.source)}</div></div>
      <div class="match-facts">${obsFacts(ref)}</div>
      <button class="btn match-choice" data-observation="${obs.key}" data-group="${g.id}">Match these jobs</button>
    </div></div>`;
}
function unmatchedCardHTML(obs){
  const candidates=matchingCandidates(obs).slice(0,5);
  return`<article class="unmatched-card">
    <div class="unmatched-head">
      <div class="unmatched-identity"><div class="match-id">${htmlEsc(obs.jobId||"No job ID")}</div>
        <strong title="${htmlEsc(obsTitle(obs))}">${htmlEsc(obsTitle(obs))}</strong>
        <small>${displayPeriod(obs.period)} · ${obs.type==="cc"?"Completed contracts":"WIP"} · ${htmlEsc(obs.source)}</small></div>
      <div class="match-facts">${obsFacts(obs)}</div>
      <button class="btn new-job-decision" data-observation="${obs.key}">Keep as a new job</button>
    </div>
    ${candidates.length?`<details class="unmatched-options"><summary><span>${candidates.length} possible match${candidates.length===1?"":"es"}</span><span class="expand-hint">Open and compare</span></summary>
      ${candidates.map(c=>candidateHTML(obs,c)).join("")}</details>`
      :'<div class="batch-meta" style="min-height:0;margin-top:9px">No plausible earlier match found.</div>'}
  </article>`;
}
function linkedGroupHTML(g){
  const snapshots=groupPeriodSnapshots(g),best=snapshots.find(o=>o.name)||snapshots[0];
  return`<details class="linked-group"><summary>
      <strong title="${htmlEsc(best.name||best.id)}">${htmlEsc(best.name||best.id)}</strong>
      <span class="periods">${snapshots.map(o=>`${o.period.slice(0,4)} ${o.type.toUpperCase()}`).join(" → ")}</span>
      <span style="font-size:11.5px;color:var(--sage-deep)">View details</span>
    </summary>
    <div class="linked-observations">${snapshots.map(o=>`<div class="linked-observation">
      <div><div class="match-id">${htmlEsc(o.id||"No job ID")}</div>
        <strong>${htmlEsc(o.name||o.id)}</strong>
        <div class="match-source">${displayPeriod(o.period)} · ${o.type==="cc"?"Completed contracts":"WIP"} · ${htmlEsc(o.authority.source)}
          ${o.deduplicated?" · CC selected over WIP duplicate":""}</div>
        <button class="btn unlink-period" data-group="${g.id}" data-period="${o.period}" style="margin-top:8px">Unlink this period</button></div>
      <div class="match-facts">${obsFacts(o)}</div></div>`).join("")}
    </div></details>`;
}
function samePeriodDuplicates(){
  return APP_STATE.batch.matchState.groups.flatMap(g=>{
    const by=new Map();
    for(const o of g.observations){
      if(!by.has(o.period))by.set(o.period,[]);
      by.get(o.period).push(o);
    }
    return[...by.entries()].filter(([,obs])=>obs.length>1)
      .map(([period,obs])=>({group:g,period,merged:mergePeriodObservations(obs)}));
  });
}
function duplicatePeriodHTML({period,merged}){
  const raw=merged.observations;
  const wipAndCc=new Set(raw.map(o=>o.type)).size>1;
  return`<details class="linked-group"><summary>
      <strong title="${htmlEsc(merged.name||merged.id)}">${htmlEsc(merged.name||merged.id)}</strong>
      <span class="periods">${displayPeriod(period)} · ${raw.map(o=>o.type.toUpperCase()).join(" + ")}</span>
      <span style="font-size:11.5px;color:var(--sage-deep)">${wipAndCc?"CC values selected":"View duplicate rows"}</span>
    </summary>
    <div class="linked-observations">${raw.map(o=>`<div class="linked-observation">
      <div><div class="match-id">${htmlEsc(o.jobId||"No job ID")}</div>
        <strong>${htmlEsc(obsTitle(o))}</strong>
        <div class="match-source">${o.type==="cc"?"Completed contracts":"WIP"} · ${htmlEsc(o.source)}</div></div>
      <div class="match-facts">${obsFacts(o)}</div></div>`).join("")}
    </div></details>`;
}
function reviewedDecisionHTML(decision){
  const obs=APP_STATE.batch.matchState.observations.find(o=>o.key===decision.obsKey);
  if(!obs)return"";
  const group=findGroupByObs(obs.key);
  const target=decision.type==="matched"
    ?group?.observations.filter(o=>o.key!==obs.key)
      .sort((a,b)=>b.period.localeCompare(a.period))[0]
    :null;
  const state=target?`Matched to ${htmlEsc(obsTitle(target))}`
    :decision.reason==="unlinked"?"Kept separate from linked history":"Kept as a new job";
  return`<details class="unmatched-card reviewed-decision" data-decision="${htmlEsc(obs.key)}"><summary>
      <div class="unmatched-identity"><div class="match-id">${htmlEsc(obs.jobId||"No job ID")}</div>
        <strong title="${htmlEsc(obsTitle(obs))}">${htmlEsc(obsTitle(obs))}</strong></div>
      <span class="decision-state">✓ ${state}</span>
      <span style="font-size:11.5px;color:var(--muted)">Review decision</span>
    </summary>
    <div class="reviewed-body"><div class="reviewed-comparison">
      <div><div class="match-source">${displayPeriod(obs.period)} · ${obs.type==="cc"?"Completed contracts":"WIP"} · ${htmlEsc(obs.source)}</div>
        <div class="match-facts">${obsFacts(obs)}</div></div>
      ${target?`<div><div class="match-source">${displayPeriod(target.period)} · ${target.type==="cc"?"Completed contracts":"WIP"} · ${htmlEsc(target.source)}</div>
        <div class="match-facts">${obsFacts(target)}</div></div>`:""}
    </div><div class="actions" style="justify-content:flex-end;margin-top:12px">
      <button class="btn undo-decision" data-observation="${obs.key}">Undo decision</button>
    </div></div></details>`;
}
function syncReviewedDecisions(){
  const root=$("#matching"),reviewed=APP_STATE.batch.matchState?.decisions||[];
  if(!root||!reviewed.length)return;
  let section=root.querySelector(".reviewed-list");
  if(!section){
    section=document.createElement("section");section.className="reviewed-list";
    const linked=root.querySelector(".matched-list");
    root.insertBefore(section,linked);
  }
  section.innerHTML=`<h3>Reviewed decisions</h3>${reviewed.map(reviewedDecisionHTML).join("")}`;
}
function showDecisionOverlay(node,message,buttonLabel="Review decision",decisionKey=null){
  if(!node)return;
  if(decisionKey)syncReviewedDecisions();
  node.classList.add("decision-dimmed");
  const overlay=document.createElement("div");overlay.className="decision-overlay";
  overlay.innerHTML=`<strong>${htmlEsc(message)}</strong><button class="btn">${htmlEsc(buttonLabel)}</button>`;
  node.appendChild(overlay);
  overlay.querySelector("button").onclick=()=>renderMatching(!decisionKey,decisionKey);
}
function renderMatching(preserveScroll=false,focusDecision=null){
  const priorY=window.scrollY;
  if(!APP_STATE.batch.matchState)buildMatchState();
  saveActiveBatchReview();APP_STATE.document.view="matching";$("#secnav").classList.add("hidden");
  const pending=APP_STATE.batch.matchState.pending;
  const pendingObs=pending.map(k=>APP_STATE.batch.matchState.observations.find(o=>o.key===k)).filter(Boolean);
  const matched=APP_STATE.batch.matchState.groups.filter(g=>new Set(g.observations.map(o=>o.period)).size>1);
  const duplicates=samePeriodDuplicates();
  const reviewed=APP_STATE.batch.matchState.decisions||[];
  $("#matching").innerHTML=`<div class="flow-head"><div><h2>Job matching</h2>
      <p>This is optional cleanup for time-series analysis. Review unresolved jobs in any order; jobs you ignore remain separate.</p></div>
      <div class="match-summary"><span>${matched.length} jobs linked across periods</span><span>${duplicates.length} same-period duplicate${duplicates.length===1?"":"s"}</span><span>${pending.length} need review</span></div></div>
    ${pendingObs.length?`<section class="unmatched-list">${pendingObs.map(unmatchedCardHTML).join("")}</section>`
      :`<div class="card" style="margin-bottom:16px"><h3>Every job has a home</h3>
        <p class="empty">Automatic matches and your decisions are included below. Every link can still be opened and undone.</p></div>`}
    ${reviewed.length?`<section class="reviewed-list"><h3>Reviewed decisions</h3>
      ${reviewed.map(reviewedDecisionHTML).join("")}</section>`:""}
    <section class="matched-list"><h3>Linked jobs</h3>
      ${matched.length?matched.map(linkedGroupHTML).join("")
        :'<p class="empty">No cross-period links yet.</p>'}
    </section>
    ${duplicates.length?`<section class="matched-list" style="margin-top:14px"><h3>Same-period duplicates</h3>
      <p class="batch-meta" style="min-height:0">These are not historical links. When a job appears in both WIP and completed contracts for the same date, completed-contract values are used in the combined table.</p>
      ${duplicates.map(duplicatePeriodHTML).join("")}</section>`:""}
    <div class="actions" style="justify-content:flex-end">
      <button class="btn" id="backToBatch">Back to analyses</button>
      <button class="btn primary" id="viewCombined">${pending.length?"Continue without matching":"View validated WIP"}</button>
    </div>`;
  $("#matching").querySelectorAll(".new-job-decision").forEach(b=>b.onclick=()=>{
    markNewJob(b.dataset.observation,false);
    showDecisionOverlay(b.closest(".unmatched-card"),"Kept as a new job","Review decision",b.dataset.observation);
  });
  $("#matching").querySelectorAll(".match-choice").forEach(b=>b.onclick=()=>{
    mergeObservation(b.dataset.observation,b.dataset.group,false);
    showDecisionOverlay(b.closest(".unmatched-card"),"Jobs linked","Review decision",b.dataset.observation);
  });
  $("#matching").querySelectorAll(".undo-decision").forEach(b=>b.onclick=()=>{
    undoMatchDecision(b.dataset.observation,false);
    showDecisionOverlay(b.closest(".reviewed-decision"),"Decision undone","Review matching");
  });
  $("#matching").querySelectorAll(".unlink-period").forEach(b=>b.onclick=()=>{
    const decisionKey=unlinkPeriodFromGroup(b.dataset.group,b.dataset.period,false);
    showDecisionOverlay(b.closest(".linked-observation"),"Job unlinked","Review decision",decisionKey);
  });
  $("#backToBatch").onclick=renderBatch;
  $("#viewCombined").onclick=renderConsolidated;
  show("matching");const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  setFlowNav("matching");
  if(focusDecision)requestAnimationFrame(()=>{
    const decision=[...$("#matching").querySelectorAll(".reviewed-decision")]
      .find(node=>node.dataset.decision===focusDecision);
    if(!decision)return;
    decision.open=true;decision.classList.add("focused");
    decision.scrollIntoView({behavior:"smooth",block:"center"});
  });
  else if(preserveScroll)requestAnimationFrame(()=>window.scrollTo(0,priorY));
  else window.scrollTo(0,0);
}
function wireFlowNav(){
  $("#navBatch").onclick=renderBatch;$("#navMatch").onclick=renderMatching;
  $("#navConsolidated").onclick=renderConsolidated;$("#navTrends").onclick=renderBatchAnalysis;
}
function mergePeriodObservations(observations){
  const ordered=[...observations].sort((a,b)=>
    (a.type==="cc"?1:0)-(b.type==="cc"?1:0)||a.itemId-b.itemId||a.row-b.row);
  const vars={},varTypes={},conflicts=[];
  for(const o of ordered)for(const[k,v]of Object.entries(o.vars)){
    if(v==null)continue;
    if(vars[k]!=null&&Math.abs(vars[k]-v)>1&&varTypes[k]===o.type)conflicts.push(k);
    vars[k]=v;varTypes[k]=o.type;
  }
  const authority=ordered[ordered.length-1]||{};
  const name=authority.jobName||ordered.find(o=>o.jobName)?.jobName||authority.label||"";
  const id=authority.jobId||ordered.find(o=>o.jobId)?.jobId||"";
  const deduplicated=new Set(ordered.map(o=>o.type)).size>1;
  const derived=deriveCanonicalVars(vars);
  /* Completed-contract billings equal total revenue. Retainage is already
     billed/receivable, not a WIP underbilling. Likewise an exactly complete WIP has no remaining
     performance through which an under/overbilling can unwind. */
  if(authority.type==="cc"||derived.P>=1-1e-6){
    derived.U=0;derived.O=0;derived.N=0;
  }
  return {id,name,vars:derived,conflicts:[...new Set(conflicts)],
    type:authority.type||"wip",authority,deduplicated,observations:ordered};
}
function consolidatedRows(period){
  return APP_STATE.batch.matchState.groups.flatMap(g=>{
    const obs=g.observations.filter(o=>o.period===period);
    if(!obs.length)return[];
    const merged=mergePeriodObservations(obs);
    const matchedPeriods=new Set(g.observations.map(o=>o.period)).size;
    return[{group:g,matchedPeriods,...merged}];
  }).sort((a,b)=>(a.type==="cc"?1:0)-(b.type==="cc"?1:0)
    ||a.authority.itemId-b.authority.itemId||a.authority.row-b.authority.row);
}
function periodAnalysisReport(period){
  const rows=consolidatedRows(period);
  const columns=PRINT_COLUMN_ORDER.map(variable=>({
    variable,header:PRINT_COLUMN_NAMES[variable],variable_name:PRINT_COLUMN_NAMES[variable]
  }));
  const jobs=rows.map(row=>({label:row.name||row.id,...row.vars}));
  return{
    source:`Validated portfolio · ${displayPeriod(period)}`,
    overall_status:"verified",validator_status:"success",findings:[],witnesses:[],
    _period:period,_consolidatedRows:rows,
    table:{
      columns,values:rows.map(row=>columns.map(col=>row.vars[col.variable]??null)),
      job_ids:rows.map(row=>row.id||""),job_names:rows.map(row=>row.name||""),
      job_labels:rows.map(row=>row.name||row.id||"")
    },
    analysis:{schema:"wip",jobs,corrections:[],coverage:{
      numeric_cols:columns.length,mapped_cols:columns.length
    }}
  };
}
function renderAnalysisScope(period,portfolio=false){
  const report=periodAnalysisReport(period);
  APP_STATE.batch.analysisScope=portfolio?"portfolio":period;
  APP_STATE.batch.analysisMode=portfolio;APP_STATE.batch.activeItem=-1;clearSourceFile();
  activateAnalysisDocument(report);
  $("#secnav").classList.add("hidden");
  renderDash(report);show("dash");
  const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  wireFlowNav();setFlowNav("timeseries");window.scrollTo(0,0);
}
function renderPeriodAnalysis(period){
  saveActiveBatchReview();
  if(batchMetadataReady()&&!APP_STATE.batch.matchState)buildMatchState();
  if(APP_STATE.batch.matchState)renderAnalysisScope(period,false);
}
function renderPortfolioAnalysis(){
  saveActiveBatchReview();
  if(batchMetadataReady()&&!APP_STATE.batch.matchState)buildMatchState();
  const periods=APP_STATE.batch.matchState?[...new Set(APP_STATE.batch.matchState.observations.map(o=>o.period))].sort():[];
  if(periods.length)renderAnalysisScope(periods[periods.length-1],true);
}
function consolidatedTableHTML(period,expanded=false){
  const rows=consolidatedRows(period);
  const cols=PRINT_COLUMN_ORDER.map(key=>[key,PRINT_COLUMN_NAMES[key]]);
  const pct=new Set(["P","PB","M"]);
  const cell=(value,key)=>value==null?"—":pct.has(key)
    ?(100*value).toFixed(1)+"%":fmt$(value);
  const hasDedupe=rows.some(r=>r.deduplicated);
  const historyCell=r=>expanded?"":`<td class="history-col">${r.matchedPeriods>1
    ?`<strong>${r.matchedPeriods} periods</strong><small>linked history</small>`:"—"}</td>`;
  const rowHTML=r=>{
    const warning=r.conflicts.length
      ?` <span class="conflict-pill" title="${htmlEsc(r.conflicts.join(", "))}">review</span>`:"";
    return`<tr>${historyCell(r)}<td>${htmlEsc(r.id||"—")}</td>
      <td title="${htmlEsc(r.name)}">${htmlEsc(r.name||"—")}${r.deduplicated?'<sup class="dedupe-mark" title="CC values replace a same-period WIP duplicate">*</sup>':""}${warning}</td>
      ${cols.map(([key])=>`<td class="num">${cell(r.vars[key],key)}</td>`).join("")}</tr>`;
  };
  const totalLine=(label,selected,className="")=>`<tr class="${className}">${expanded?"":'<td class="history-col"></td>'}
    <td></td><td>${label}</td>${cols.map(([key])=>
      `<td class="num">${pct.has(key)?"":cell(selected.reduce((sum,r)=>sum+(r.vars[key]||0),0),key)}</td>`).join("")}</tr>`;
  const sections=[
    ["Work in progress",rows.filter(r=>r.type!=="cc"),"WIP total"],
    ["Completed contracts",rows.filter(r=>r.type==="cc"),"CC total"]
  ].filter(([,selected])=>selected.length);
  const showCombined=sections.length===2;
  const span=cols.length+(expanded?2:3);
  const body=sections.map(([label,selected,total])=>
    `<tr class="group-row"><td colspan="${span}">${label}</td></tr>
      ${selected.map(rowHTML).join("")}${totalLine(total,selected,"section-total")}`).join("");
  const tableClass=expanded?"compact-table":"scroll-table";
  return`<div class="tablewrap"><table class="consolidated-table ${tableClass}"><thead><tr>
    ${expanded?"":'<th class="history-col">Matched</th>'}<th>Job<br>ID</th><th>Job name</th>
    ${cols.map(([key,name])=>`<th title="${htmlEsc(name)}">${expanded?COMPACT_COLUMN_NAMES[key]:htmlEsc(name)}</th>`).join("")}
    </tr></thead><tbody>${body}</tbody>
    ${showCombined?`<tfoot>${totalLine("Combined total",rows)}</tfoot>`:""}</table></div>
    ${hasDedupe?'<p class="dedupe-key">* Completed-contract values replace the same-period WIP duplicate.</p>':""}`;
}
function renderConsolidated(){
  if(!APP_STATE.batch.matchState)buildMatchState();
  const periods=[...new Set(APP_STATE.batch.matchState.observations.map(o=>o.period))].sort();
  const current=$("#periodSelect")?.value||periods[periods.length-1];
  $("#consolidated").innerHTML=`<div class="flow-head"><div><h2>Validated WIP</h2>
      <p>WIP and completed-contract schedules stay separate during validation, then collapse into one deduplicated job table here.</p></div>
      <div style="min-width:220px"><select class="period-select" id="periodSelect" aria-label="Reporting period">
        ${periods.map(p=>`<option value="${p}" ${p===current?"selected":""}>${displayPeriod(p)}</option>`).join("")}</select></div></div>
    <section class="table-card" id="combinedTable">
      <div class="table-card-head"><div><h3>Combined reporting-period table</h3>
        <p>Validated values plus deterministic WIP calculations</p></div>
        <button class="btn" id="expandCombined">Expand</button></div>
      <div id="combinedTableBody">${consolidatedTableHTML(current)}</div>
    </section>
    <div class="actions" style="justify-content:flex-end">
      <button class="btn" id="printCombined">Print validated WIP</button>
      <button class="btn primary" id="viewTrends">View underwriting analysis</button></div>`;
  $("#periodSelect").onchange=e=>$("#combinedTableBody").innerHTML=consolidatedTableHTML(e.target.value);
  $("#printCombined").onclick=()=>printConsolidated($("#periodSelect").value);
  $("#expandCombined").onclick=()=>openConsolidatedModal($("#periodSelect").value);
  $("#viewTrends").onclick=renderBatchAnalysis;
  show("consolidated");const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  setFlowNav("consolidated");window.scrollTo(0,0);
}
function singleValidatedTableHTML(rep){
  const count=tableJobCount(rep?.table);
  const rows=Array.from({length:count},(_,index)=>{
    const identity=tableIdentity(rep.table,index);
    return {label:identity.jobName||identity.jobId||identity.label||`Row ${index+1}`,
      vars:canonicalVars(rep,index,APP_STATE.document.accepted)};
  });
  const columns=PRINT_COLUMN_ORDER.filter(variable=>rows.some(row=>
    Number.isFinite(+row.vars[variable])));
  if(!rows.length||!columns.length)return`<p class="empty">No validated schedule is available.</p>`;
  const pct=new Set(["P","PB","M"]);
  const cell=(value,variable)=>!Number.isFinite(+value)?"—":pct.has(variable)
    ?(100*(+value)).toFixed(1)+"%":fmt$(+value);
  const body=rows.map(row=>`<tr><td class="job-col" title="${htmlEsc(row.label)}">${htmlEsc(row.label)}</td>
    ${columns.map(variable=>`<td class="num">${cell(row.vars[variable],variable)}</td>`).join("")}</tr>`).join("");
  const foot=`<tr><td class="job-col">Total</td>${columns.map(variable=>`<td class="num">${pct.has(variable)?"":cell(rows.reduce((sum,row)=>sum+(Number.isFinite(+row.vars[variable])?+row.vars[variable]:0),0),variable)}</td>`).join("")}</tr>`;
  return `<div class="tablewrap"><table class="consolidated-table scroll-table single-validated-table"><thead><tr>
    <th class="job-col">Job</th>${columns.map(variable=>`<th>${htmlEsc(PRINT_COLUMN_NAMES[variable]||variableName(variable))}</th>`).join("")}
    </tr></thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table></div>`;
}
function renderSingleValidatedWip(){
  syncActiveSectionReview();const combined=documentAnalysisReport();
  const scope=combined._combinedSectionCount>1?` Combined across ${combined._combinedPageLabel}.`:"";
  const manual=combined.overall_status==="user_mapped_unverified";
  $("#consolidated").innerHTML=`<div class="flow-head"><div><h2>Validated WIP</h2>
      <p>${manual
        ?"The reviewed column mapping and deterministic WIP calculations in one table."
        :"The mathematically identified schedule, accepted corrections, and deterministic WIP calculations in one table."}${scope}</p></div></div>
    <section class="table-card"><div class="table-card-head"><div><h3>Validated schedule</h3>
      <p>Standardized column names; document headers remain page-specific on the Validation page.</p></div></div>
      ${singleValidatedTableHTML(combined)}</section>
    <div class="actions" style="justify-content:flex-end"><button class="btn" id="singleValidation">Review validation</button>
      <button class="btn primary" id="singleAnalysis">View underwriting analysis</button></div>`;
  $("#singleValidation").onclick=()=>{activateDocumentSection(APP_STATE.document.activeSection);renderSecnav();renderCertificate(APP_STATE.document.report);show("certificate");setSingleNav("validation");window.scrollTo(0,0)};
  $("#singleAnalysis").onclick=renderDocumentAnalysis;
  show("consolidated");const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  setSingleNav("validated");window.scrollTo(0,0);
}
function openConsolidatedModal(period){
  const m=document.createElement("div");m.className="wipmodal validated-modal";
  m.innerHTML=`<div class="wm-panel" role="dialog" aria-modal="true" aria-label="Full validated WIP">
    <div class="wm-head"><h3>Validated WIP <small>${displayPeriod(period)} · full calculated schedule</small></h3>
      <div class="wm-legend"></div>
      <button class="btn" id="wmClose">Close</button></div>
    <div class="wm-body">${consolidatedTableHTML(period,true)}</div></div>`;
  document.body.appendChild(m);document.body.style.overflow="hidden";
  const close=()=>{m.remove();document.body.style.overflow="";document.removeEventListener("keydown",esc)};
  const esc=e=>{if(e.key==="Escape")close()};
  document.addEventListener("keydown",esc);
  m.onclick=e=>{if(e.target===m)close()};
  m.querySelector("#wmClose").onclick=close;
}
function groupPeriodSnapshots(g){
  const by=new Map();
  for(const o of g.observations){
    if(!by.has(o.period))by.set(o.period,[]);
    by.get(o.period).push(o);
  }
  return[...by.entries()].sort(([a],[b])=>a.localeCompare(b))
    .map(([period,obs])=>({period,...mergePeriodObservations(obs)}));
}
function medianNumber(values){
  const x=values.filter(Number.isFinite).sort((a,b)=>a-b);
  if(!x.length)return null;
  const m=Math.floor(x.length/2);
  return x.length%2?x[m]:(x[m-1]+x[m])/2;
}
function timeSeriesData(){
  if(!APP_STATE.batch.matchState){
    if(!batchMetadataReady())return null;
    buildMatchState();
  }
  const series=APP_STATE.batch.matchState.groups.map(g=>({group:g,snapshots:groupPeriodSnapshots(g)}))
    .filter(x=>x.snapshots.length>1);
  if(!series.length)return null;

  /* Build every linked job first. Anomaly detection is deliberately hybrid:
     a large absolute + margin swing always flags, while a robust median/MAD
     comparison catches contractor-specific outliers that miss the fixed gate. */
  const allRows=series.map(({group,snapshots:s})=>{
    const first=s[0],last=s[s.length-1],profit=x=>
      x.vars.G??(x.vars.V!=null&&x.vars.C!=null?x.vars.V-x.vars.C:null);
    const p0=profit(first),p1=profit(last);
    const v0=first.vars.V,v1=last.vars.V;
    const scale=Math.max(Math.abs(v0||0),Math.abs(v1||0),1);
    const margin0=p0!=null&&v0? p0/v0:null;
    const margin1=p1!=null&&v1? p1/v1:null;
    const marginChange=margin0!=null&&margin1!=null?margin1-margin0:null;
    const cost=first.vars.C!=null&&last.vars.C!=null?last.vars.C-first.vars.C:null;
    const fade=p0!=null&&p1!=null?p1-p0:null;
    const stalled=first.vars.D!=null&&last.vars.D!=null&&first.vars.B!=null&&last.vars.B!=null
      &&Math.abs(last.vars.D-first.vars.D)<=1&&Math.abs(last.vars.B-first.vars.B)<=1;
    const materialCost=cost!=null&&cost>Math.max(10000,.03*Math.abs(first.vars.C||0));
    const materialFade=fade!=null&&fade<-Math.max(10000,.03*Math.abs(p0||0));
    return{groupId:group.id,name:last.name||first.name,id:last.id||first.id,from:first.period,to:last.period,
      fromType:first.type,toType:last.type,contract:v0!=null&&v1!=null?v1-v0:null,
      scale,priorProfit:p0,currentProfit:p1,margin0,margin1,marginChange,
      cost,fade,stalled,materialCost,materialFade};
  });

  const marginChanges=allRows.map(r=>r.marginChange).filter(Number.isFinite);
  const marginMedian=medianNumber(marginChanges);
  const marginMad=marginMedian==null?null:
    medianNumber(marginChanges.map(x=>Math.abs(x-marginMedian)));
  const robustReady=marginChanges.length>=5&&marginMad!=null&&marginMad>.002;

  for(const r of allRows){
    if(r.fade==null||r.marginChange==null){
      r.estimateAnomaly=false;r.anomalyZ=null;r.swingMultiple=null;continue;
    }
    const absoluteSwing=Math.abs(r.fade);
    const marginSwing=Math.abs(r.marginChange);
    const fixedMaterial=absoluteSwing>=Math.max(50000,.05*r.scale);
    const fixedExtreme=fixedMaterial&&marginSwing>=.10;
    const base=Math.max(Math.abs(r.priorProfit||0),.01*r.scale);
    r.swingMultiple=absoluteSwing/base;
    const multipleExtreme=fixedMaterial&&r.swingMultiple>=5;
    r.anomalyZ=robustReady
      ?0.6745*Math.abs(r.marginChange-marginMedian)/marginMad:null;
    const portfolioExtreme=r.anomalyZ!=null&&r.anomalyZ>=4
      &&absoluteSwing>=Math.max(50000,.03*r.scale)&&marginSwing>=.05;
    r.estimateAnomaly=fixedExtreme||multipleExtreme||portfolioExtreme;
    r.anomalyDirection=r.fade>=0?"profit gain":"profit loss";
    r.anomalyReason=fixedExtreme
      ?`${pct1(marginSwing)} margin swing on ${fmtM(absoluteSwing)} of gross profit`
      :multipleExtreme
        ?`${r.swingMultiple.toFixed(r.swingMultiple>=10?0:1)}× the prior expected-profit base`
        :portfolioExtreme
          ?`${r.anomalyZ.toFixed(1)} robust deviations from the contractor's typical margin change`
          :"";
  }

  const rows=allRows.filter(r=>
    r.materialCost||r.materialFade||r.stalled||r.estimateAnomaly);
  if(!rows.length)return null;

  const costIncreases=rows.filter(r=>r.materialCost);
  const totalCostIncrease=costIncreases.reduce((s,r)=>s+r.cost,0);
  const fading=rows.filter(r=>r.materialFade);
  const fade=fading.reduce((s,r)=>s-r.fade,0);
  const anomalies=rows.filter(r=>r.estimateAnomaly)
    .sort((a,b)=>Math.abs(b.fade||0)-Math.abs(a.fade||0));
  const stalls=rows.filter(r=>r.stalled).length;
  return{rows,totalCostIncrease,costIncreaseJobs:costIncreases.length,
    fade,fadeJobs:fading.length,stalls,anomalyJobs:anomalies.length,
    largestAnomaly:anomalies[0]||null};
}
function earlyUnderbillingFadeData(){
  /* Conditional pattern from the contractor's own linked history: among jobs
     observed materially underbilled (<= -2% of contract) before 60% complete,
     how often did margin fade by the end? Evidence = jobs now past 85%
     complete with a usable margin at both points. At-risk = open jobs whose
     current or past observations match the early-underbilled profile. */
  if(!APP_STATE.batch.matchState)return null;
  const EARLY_MIN=.15,EARLY_MAX=.60,UB=-.02,LATE=.85,FADE=-.01;
  const evidence=[],atRisk=[];
  for(const g of APP_STATE.batch.matchState.groups){
    const snaps=groupPeriodSnapshots(g).map(x=>jmSnapshotPoint(x.vars,x.period,x.type));
    if(!snaps.length)continue;
    const early=snaps.find(p=>Number.isFinite(p.P)&&Number.isFinite(p.position)
      &&p.P>=EARLY_MIN&&p.P<=EARLY_MAX&&p.position<=UB);
    if(!early)continue;
    const last=snaps[snaps.length-1];
    const authority=g.observations[g.observations.length-1]||{};
    const label=authority.jobName||authority.name||authority.jobId||authority.id||"Job";
    if(Number.isFinite(last.P)&&last.P>=LATE&&snaps.length>1){
      if(Number.isFinite(early.margin)&&Number.isFinite(last.margin))
        evidence.push({groupId:g.id,label,fadePts:last.margin-early.margin});
    }else atRisk.push({groupId:g.id,label});
  }
  if(evidence.length<3)return null;
  const faded=evidence.filter(e=>e.fadePts<=FADE);
  const avgFadePts=faded.length?faded.reduce((sum,e)=>sum+e.fadePts,0)/faded.length:0;
  return{evidence:evidence.length,faded:faded.length,avgFadePts,atRisk};
}
function eubSentenceHTML(eub,linkable){
  if(!eub)return"";
  const list=eub.atRisk.map(j=>linkable
    ?`<button class="jm-link eub-job" data-group="${htmlEsc(j.groupId)}">${htmlEsc(j.label)}</button>`
    :htmlEsc(j.label)).join(", ");
  return`<p class="eub-note"><strong>Early underbillings and later fade:</strong> on this contractor's linked history, jobs underbilled more than 2% of contract before 60% complete went on to lose margin in ${eub.faded} of ${eub.evidence} cases${eub.faded?` (average ${(100*eub.avgFadePts).toFixed(1)} pts)`:""}. ${eub.atRisk.length?`${eub.atRisk.length} open job${eub.atRisk.length===1?"":"s"} currently match${eub.atRisk.length===1?"es":""} that profile: ${list}.`:"No open jobs currently match that profile."}</p>`;
}
function timeSeriesKpiStripHTML(rep){
  const data=timeSeriesData();
  const jobBorrow=computeSignals(rep).find(s=>s.id==="job_borrow")||null;
  if(!data&&!jobBorrow)return"";
  const{totalCostIncrease=0,costIncreaseJobs=0,fade=0,fadeJobs=0,
    anomalyJobs=0,largestAnomaly=null}=data||{};
  const js=n=>`${n} job${n===1?"":"s"}`;
  const cards=[
    fadeJobs?`<div class="kpi tint-brick" title="${js(fadeJobs)} lost expected profit between statements"><div class="lab">Profit fade · ${js(fadeJobs)}</div><div class="val num">${fmtM(fade)}</div></div>`:"",
    costIncreaseJobs?`<div class="kpi" title="${js(costIncreaseJobs)} revised estimated cost upward"><div class="lab">Cost increases · ${js(costIncreaseJobs)}</div><div class="val num">${fmtM(totalCostIncrease)}</div></div>`:"",
    `<div class="kpi${jobBorrow?" tint-amber":""}" title="${jobBorrow?`${js(jobBorrow.count)} with billings pulled forward beyond cost plus projected profit`:"No billings pulled forward beyond cost and projected profit"}"><div class="lab">Job borrow${jobBorrow?` · ${js(jobBorrow.count)}`:""}</div><div class="val num">${fmtM(jobBorrow?.dollars||0)}</div></div>`,
    `<div class="kpi${anomalyJobs?" tint-amber":""}" title="${largestAnomaly?`Largest GP swing ${fmtM(Math.abs(largestAnomaly.fade))}`:"No extreme cross-period estimate changes"}"><div class="lab">Estimate anomalies</div><div class="val num">${anomalyJobs}</div></div>`
  ].filter(Boolean).join("");
  return cards?`<div class="ts-kpi-label">Across reporting periods</div><div class="kpis trend-kpis">${cards}</div>`:"";
}
function timeSeriesSectionHTML(rep){
  const data=timeSeriesData();
  const eub=earlyUnderbillingFadeData();
  if(!data&&!eub)return"";
  const{rows=[],stalls=0}=data||{};
  if(!rows.length&&!stalls&&!eub)return"";
  const flagCell=(on,cls,label)=>`<td class="ts-flag-cell">${on?`<span class="ts-flag ${cls}" title="${htmlEsc(label)}"></span>`:""}</td>`;
  return`<div class="time-series-section"><h4>Jobs with changes between statements
      <small>Material changes, stalled jobs, and extreme estimate movements are shown; anomalies use both fixed materiality gates and the contractor’s own history</small></h4>
    ${eubSentenceHTML(eub,true)}
    ${rows.length?`<div class="tablewrap"><table><thead><tr><th>Job ID</th><th>Job name</th>
      <th class="ts-flag-col" title="Profit fade">Fade</th><th class="ts-flag-col" title="Cost estimate increase">Cost ↑</th>
      <th class="ts-flag-col" title="Estimate anomaly">Anom.</th><th class="ts-flag-col" title="No cost or billing movement">Stall</th>
      <th>Periods</th><th>Contract Δ</th><th>Cost est. Δ</th><th>Prior GP</th><th>Latest GP</th>
      <th>GP Δ</th><th>Margin Δ</th></tr></thead><tbody>
      ${rows.map(r=>{const focus=reportRowForGroup(rep,r.groupId);return`<tr class="${r.estimateAnomaly?"anomaly-row":""}" data-group="${htmlEsc(r.groupId||"")}"${focus>=0?` data-row="${focus}"`:""} title="View job analysis"><td>${htmlEsc(r.id||"—")}</td><td>${htmlEsc(r.name||"—")}</td>
        ${flagCell(r.materialFade,"brick","Profit fade")}
        ${flagCell(r.materialCost,"sage","Cost estimate increase")}
        ${flagCell(r.estimateAnomaly,"amber",`${r.fade>=0?"Profit gain anomaly":"Profit loss anomaly"}${r.anomalyReason?": "+r.anomalyReason:""}`)}
        ${flagCell(r.stalled,"mut","No cost or billing movement across periods")}
        <td>${r.from.slice(0,4)} → ${r.to.slice(0,4)}</td>
        <td class="num">${r.contract==null?"—":fmtM(r.contract)}</td>
        <td class="num">${r.cost==null?"—":fmtM(r.cost)}</td>
        <td class="num">${r.priorProfit==null?"—":fmtM(r.priorProfit)}</td>
        <td class="num">${r.currentProfit==null?"—":fmtM(r.currentProfit)}</td>
        <td class="num" style="${r.fade<0?"color:var(--brick)":""}">${r.fade==null?"—":fmtM(r.fade)}</td>
        <td class="num">${r.marginChange==null?"—":`${r.marginChange>=0?"+":""}${pct1(r.marginChange)}`}</td></tr>`}).join("")}
      </tbody></table></div>`:""}
      ${stalls?`<p class="batch-needed" style="margin-top:10px">${stalls} linked job${stalls===1?" has":"s have"} no cost or billing movement across the available periods.</p>`:""}
    </div>`;
}
function renderBatchAnalysis(){
  renderPortfolioAnalysis();
}
function renderTimeSeries(){
  renderBatchAnalysis();
}

