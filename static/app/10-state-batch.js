let BATCH_ITEMS=[],BATCH_MODE=false,BATCH_RUN=null;
let COLUMN_MAPPING_STATE=new Map();
let BILLING_TRAJECTORY_FILTERS=new Set(["trapped_cash","profit_fade"]);
let BILLING_TRAJECTORY_HIDDEN=new Set();
/* One healthy-billing band, shared by the portfolio trajectory chart and the
   job analysis modal so the two can never disagree. Tune here only.
   Each row: [% complete, top of band, bottom of band] as fraction of contract. */
const HEALTHY_BILLING_CORRIDOR=[
  [.15,.065,-.015],[.25,.07,-.012],[.40,.06,-.015],
  [.60,.042,-.018],[.80,.024,-.012],[1,.004,-.003]
];

function tableValues(t){
  if(!t)return[];
  if(Array.isArray(t.values))return t.values;
  return(t.rows||[]).map(r=>(t.columns||[]).map(c=>r.values?.[c.variable]??null));
}
function tableLabels(t){
  if(!t)return[];
  if((t.job_ids||t.job_names)&&tableJobCount(t)){
    const n=tableJobCount(t);
    return Array.from({length:n},(_,i)=>{
      const row=(t.rows||[])[i]||{},id=(t.job_ids||[])[i]||row.job_id||"";
      const name=(t.job_names||[])[i]||row.job_name||"";
      return id&&name?`${id} · ${name}`:(name||id||"");
    });
  }
  if(Array.isArray(t.job_labels))return t.job_labels;
  return(t.rows||[]).map(r=>r.label??"");
}
function tableJobCount(t){return tableValues(t).length}
function documentSections(doc){
  return doc?.tables
    ?doc.tables.flatMap(t=>(t.sections||[]).map(s=>({type:s.type||s.schema||"wip",rep:s.report||{}})))
    :[{type:(doc?.analysis?.schema||"wip"),rep:doc||{}}];
}
function initBatchMetadata(item){
  const d=item.doc?.document||{};
  const types=[...new Set(documentSections(item.doc).map(s=>s.type).filter(Boolean))];
  item.periodEnd=d.reporting_date||"";
  item.scheduleType=types.length===1?types[0]:(types.length>1?"mixed":"");
}
function displayPeriod(iso){
  if(!iso)return"Date required";
  const [y,m,d]=iso.split("-").map(Number);
  return new Date(Date.UTC(y,m-1,d)).toLocaleDateString(undefined,
    {year:"numeric",month:"long",day:"numeric",timeZone:"UTC"});
}
function batchMetadataReady(){
  return BATCH_ITEMS.filter(x=>x.status==="ready")
    .every(x=>Boolean(x.periodEnd&&x.scheduleType));
}
function setFlowNav(active){
  const pairs=[["navBatch","batch"],["navMatch","matching"],
    ["navConsolidated","consolidated"],["navTrends","timeseries"]];
  for(const[id,key]of pairs){
    const el=$("#"+id);el.classList.toggle("hidden",key!=="batch"&&!APP_STATE.batch.matchState);
    el.style.borderColor=key===active?"var(--sage-deep)":"";
  }
  $("#navCert").classList.add("hidden");$("#navDash").classList.add("hidden");
}

function setSingleNav(active){
  for(const id of ["navBatch","navMatch","navTrends"])
    $("#"+id).classList.add("hidden");
  const pairs=[["navCert","validation"],["navDash","analysis"],
    ["navConsolidated","validated"]];
  for(const[id,key]of pairs){
    const el=$("#"+id);el.classList.remove("hidden");
    el.style.borderColor=key===active?"var(--sage-deep)":"";
  }
}

function resetBatch(){
  BATCH_ITEMS=[];APP_STATE.batch.activeItem=-1;BATCH_MODE=false;BATCH_RUN=null;APP_STATE.batch.matchState=null;
  COLUMN_MAPPING_STATE=new Map();
  APP_STATE.batch.analysisMode=false;APP_STATE.batch.analysisScope="portfolio";
  BILLING_TRAJECTORY_FILTERS=new Set(["trapped_cash","profit_fade"]);
  APP_STATE.billingTrajectory.showAll=false;
}
function runDuration(seconds){
  if(!(seconds>0))return"";
  if(seconds<60)return`${seconds.toFixed(1)}s`;
  const m=Math.floor(seconds/60);
  return`${m}m ${Math.round(seconds-60*m)}s`;
}
/* CSV runs do not make a model call, so the backend has no model elapsed time
   to report. Fill only that blank with the browser wall clock; model-backed
   reports keep their existing backend duration. Apply it to every section so
   the same footer works for single documents and batch review. */
function attachClientElapsed(doc,seconds){
  if(!doc||!(seconds>0))return doc;
  const apply=rep=>{
    if(!rep||typeof rep!=="object")return;
    rep.metrics=rep.metrics||{};
    if(!(+(rep.metrics.elapsed_seconds)>0))rep.metrics.elapsed_seconds=seconds;
  };
  apply(doc);
  (doc.tables||[]).forEach(t=>(t.sections||[]).forEach(s=>apply(s.report)));
  return doc;
}
/* One reference line under the batch: what the whole upload cost in time and
   money. The elapsed figure is the batch's wall clock -- summing the
   documents' own elapsed_seconds would double-count the overlap they were
   run with, and report a number nobody waited. */
function batchRunFoot(){
  const bits=[];
  if(BATCH_ITEMS.length)
    bits.push(`${BATCH_ITEMS.length} document${BATCH_ITEMS.length===1?"":"s"}`);
  const elapsed=runDuration(BATCH_RUN?.seconds);
  if(elapsed)bits.push(elapsed);
  const metrics=BATCH_ITEMS.map(x=>x.doc?.metrics).filter(Boolean);
  const calls=metrics.reduce((n,m)=>n+(+m.api_calls||0),0);
  const cost=metrics.reduce((n,m)=>n+(+m.cost_usd||0),0);
  if(calls)bits.push(`${calls} model call${calls===1?"":"s"}`);
  if(cost)bits.push(`total $${cost.toFixed(4)}`);
  return bits.join(" · ");
}
function batchDocError(doc){
  if(!doc)return"Processing did not return a report.";
  if(doc.overall_status==="pipeline_error")
    return doc.validator_reason||"The processing pipeline stopped unexpectedly.";
  if(doc.overall_status==="extraction_failed")
    return"Could not transcribe this document.";
  if(doc.tables&&!doc.tables.length)return"No WIP or completed-contracts table was found.";
  return"";
}
function batchDocSummary(doc){
  const reps=doc.tables
    ?doc.tables.flatMap(t=>(t.sections||[]).map(s=>s.report||{}))
    :[doc];
  const jobs=reps.reduce((n,r)=>n+((r.table?.values||r.table?.rows||[]).length),0);
  const schedules=reps.length;
  return `${jobs} job${jobs===1?"":"s"} across ${schedules} table${schedules===1?"":"s"}`;
}
function batchValidation(item){
  const sections=documentSections(item.doc);
  const incomplete=sections.some(section=>["unmapped","header_mapped_unverified","llm_mapped_unverified"].includes(section.rep.overall_status));
  if(incomplete)return{kind:"review",icon:"!",text:"Validation needs review"};
  if(sections.some(section=>section.rep.overall_status==="user_mapped_unverified"))
    return{kind:"corrected",icon:"\u2713",text:"Column mapping reviewed"};
  let checksPassed=0,checksTotal=0,nBad=0,nFixed=0,jobFixes=0,totalFixes=0;
  sections.forEach((section,si)=>{
    const rep=section.rep||{};
    const corrs=rep.analysis?.corrections||[];
    const selected=item.reviewState?.states?.[si]||defaultAcceptedCorrections(rep);
    const counts=computeValidationChecks(rep,selected);
    checksPassed+=counts.passed;checksTotal+=counts.checks.length;
    nBad+=counts.nBad;nFixed+=counts.nFixed;
    jobFixes+=(rep.findings||[]).filter(f=>{
      const ci=corrs.findIndex(x=>x.label===f.row_label&&x.implied===f.proposed_correction);
      return ci>=0&&selected.has(ci);
    }).length;
    totalFixes+=(counts.td?.totalCorrections||[]).filter(t=>selected.has(t.correctionKey)).length;
  });
  const tally=checksTotal?` · ${checksPassed}/${checksTotal} checks passed`:"";
  if(nBad)return{kind:"review",icon:"!",text:`${nBad} check${nBad===1?"":"s"} need review`+tally};
  if(nFixed){
    const parts=[];
    if(jobFixes)parts.push(`${jobFixes} cell correction${jobFixes===1?"":"s"}`);
    if(totalFixes)parts.push(`${totalFixes} stated-total correction${totalFixes===1?"":"s"}`);
    return{kind:"corrected",icon:"✓",text:"Validated after corrections"+tally,
      note:parts.length?parts.join(" · "):"Corrections applied"};
  }
  return{kind:"ok",icon:"✓",text:"All checks passed"+tally};
}
/* Documents are independent runs on the server -- nothing in the graph is
   shared between them -- so a batch is limited only by how many streams we
   are willing to hold open. Four keeps us well inside the browser's per-host
   connection cap while turning a 4-document wait into roughly one. */
const BATCH_CONCURRENCY=4;
async function scanBatch(files){
  resetBatch();clearSourceFile();BATCH_MODE=true;
  BATCH_ITEMS=files.map((file,i)=>({id:i,file,name:file.name||`Schedule ${i+1}`,
    status:"queued",doc:null,error:"",line:"Queued",reviewState:null,
    periodEnd:"",scheduleType:""}));
  show("processing");resetProgressStage();renderBatchLanes();
  showBatchProgress(0,BATCH_ITEMS.length);
  $("#nav").classList.add("hidden");$("#tagline").classList.remove("hidden");
  APP_STATE.progress.running=true;
  const model=$("#model")?.value||"";
  const startedAt=performance.now();
  let next=0,completed=0,running=0;
  const tick=()=>showBatchProgress(completed,BATCH_ITEMS.length,running);
  async function worker(){
    while(next<BATCH_ITEMS.length){
      const i=next++,item=BATCH_ITEMS[i];
      item.status="processing";item.line="Reading and validating schedule";
      running++;updateBatchLane(i);tick();
      const itemStartedAt=performance.now();
      const fd=new FormData();fd.append("file",item.file);
      if(model)fd.append("model",model);
      try{
        item.doc=await readStream(API+"/api/scan",{method:"POST",body:fd},
          message=>{item.line=message;updateBatchLane(i);});
        attachClientElapsed(item.doc,(performance.now()-itemStartedAt)/1000);
        item.error=batchDocError(item.doc);
        item.status=item.error?"failed":"ready";
        if(item.status==="ready")initBatchMetadata(item);
        item.line=item.status==="ready"?"Ready for review":"Needs attention";
      }catch(e){
        item.error=String(e);item.status="failed";
        item.line="Could not be processed";
      }
      running--;completed++;updateBatchLane(i);tick();
    }
  }
  const lanes=Math.min(BATCH_CONCURRENCY,BATCH_ITEMS.length);
  await Promise.all(Array.from({length:lanes},worker));
  // Wall clock, not the sum of the documents': they overlapped on purpose.
  BATCH_RUN={seconds:(performance.now()-startedAt)/1000};
  APP_STATE.progress.running=false;finishProgressLine();APP_STATE.batch.activeItem=-1;
  setTimeout(()=>{hideBatchLanes();renderBatch();},450);
}
function saveActiveBatchReview(){
  if(!BATCH_MODE||APP_STATE.document.view==="batch"||APP_STATE.batch.activeItem<0||!BATCH_ITEMS[APP_STATE.batch.activeItem])return;
  APP_STATE.document.correctionsBySection[APP_STATE.document.activeSection]=APP_STATE.document.accepted;
  BATCH_ITEMS[APP_STATE.batch.activeItem].reviewState={
    states:APP_STATE.document.correctionsBySection.map(s=>new Set(s)),active:APP_STATE.document.activeSection,
    view:APP_STATE.document.view==="dash"?"dash":"certificate"
  };
}
function renderBatch(){
  if(!BATCH_MODE)return;
  saveActiveBatchReview();APP_STATE.document.view="batch";APP_STATE.batch.analysisMode=false;clearSourceFile();
  $("#secnav").classList.add("hidden");
  const ready=BATCH_ITEMS.filter(x=>x.status==="ready").length;
  const failed=BATCH_ITEMS.length-ready;
  const readyItems=BATCH_ITEMS.filter(x=>x.status==="ready");
  const byPeriod=new Map();
  for(const item of readyItems){
    const key=item.periodEnd||"";
    if(!byPeriod.has(key))byPeriod.set(key,[]);
    byPeriod.get(key).push(item);
  }
  const failedItems=BATCH_ITEMS.filter(x=>x.status!=="ready");
  const groupHTML=[...byPeriod.entries()]
    .sort(([a],[b])=>(a||"9999").localeCompare(b||"9999"))
    .map(([period,items])=>`<div class="batch-period">
      <div class="batch-period-head"><h3>${displayPeriod(period)}</h3>
        <span>${items.length} document${items.length===1?"":"s"}</span></div>
      <div class="batch-grid">${items.map(item=>batchCardHTML(item,BATCH_ITEMS.indexOf(item))).join("")}</div>
    </div>`).join("");
  const failedHTML=failedItems.length?`<div class="batch-period">
    <div class="batch-period-head"><h3>Needs attention</h3></div>
    <div class="batch-grid">${failedItems.map(item=>batchCardHTML(item,BATCH_ITEMS.indexOf(item))).join("")}</div>
  </div>`:"";
  $("#batch").innerHTML=`<div class="batch-intro">
    <div><h2>${ready} schedule${ready===1?"":"s"} ready</h2>
      <p>Open any underwriting analysis immediately. Job matching is optional and only improves the combined time-series view.</p></div>
    <div class="batch-intro-actions">
      ${failed?`<span class="batch-state" style="background:#F2DDD6;color:var(--brick)">${failed} need${failed===1?"s":""} attention</span>`:""}
      ${ready?`<button class="btn quiet-btn" id="optionalMatching" ${batchMetadataReady()?"":"disabled"}>Job matching <span style="color:var(--muted)">(optional)</span></button>`:""}
      ${ready?`<button class="btn primary" id="viewCombinedNow" ${batchMetadataReady()?"":"disabled"}>
        View underwriting analysis <span class="next-arrow" aria-hidden="true">→</span></button>`:""}
    </div>
  </div>
  ${groupHTML}${failedHTML}
  <p class="foot">${batchRunFoot()}</p>
`;
  $("#batch").querySelectorAll(".batch-review").forEach(b=>b.onclick=()=>reviewBatchItem(+b.dataset.batch,"certificate"));
  $("#batch").querySelectorAll(".batch-analysis").forEach(b=>b.onclick=()=>renderBatchItemAnalysis(+b.dataset.batch));
  $("#batch").querySelectorAll(".batch-date").forEach(el=>el.onchange=()=>{
    BATCH_ITEMS[+el.dataset.batch].periodEnd=el.value;APP_STATE.batch.matchState=null;renderBatch();});
  $("#batch").querySelectorAll(".batch-type").forEach(el=>el.onchange=()=>{
    BATCH_ITEMS[+el.dataset.batch].scheduleType=el.value;APP_STATE.batch.matchState=null;renderBatch();});
  const optional=$("#optionalMatching");if(optional)optional.onclick=()=>{buildMatchState();renderMatching();};
  const combined=$("#viewCombinedNow");if(combined)combined.onclick=()=>{buildMatchState();renderBatchAnalysis();};
  show("batch");
  const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  setFlowNav("batch");
  $("#navBatch").onclick=renderBatch;
  window.scrollTo(0,0);
}
function batchCardHTML(item,i){
  if(item.status!=="ready")return`<article class="batch-card failed">
    <div class="batch-top"><span class="batch-name" title="${htmlEsc(item.name)}">${htmlEsc(item.name)}</span>
      <span class="batch-state">needs attention</span></div>
    <p class="batch-meta">${htmlEsc(item.error||"Could not process this schedule.")}</p></article>`;
  const validation=batchValidation(item);
  return`<article class="batch-card ${validation.kind==="review"?"needs-review":""}">
    <div class="batch-top"><span class="batch-name" title="${htmlEsc(item.name)}">${htmlEsc(item.name)}</span>
      <button class="btn batch-analysis" data-batch="${i}">View independent analysis</button></div>
    <div class="batch-validation ${validation.kind}"><span class="validation-icon">${validation.icon}</span>
      <span class="batch-validation-copy"><span>${validation.text}</span>
        ${validation.note?`<span class="batch-validation-note">${validation.note}</span>`:""}</span>
      <button class="btn batch-review" data-batch="${i}">Review validation</button></div>
    <div class="batch-fields">
      <div class="batch-field ${item.periodEnd?"":"missing"}"><label>Reporting date</label>
        <input class="batch-date" data-batch="${i}" type="date" value="${htmlEsc(item.periodEnd)}" aria-label="Reporting date for ${htmlEsc(item.name)}">
        ${item.periodEnd?"":'<div class="batch-needed">Date required</div>'}</div>
      <div class="batch-field ${item.scheduleType?"":"missing"}"><label>Schedule type</label>
        <select class="batch-type" data-batch="${i}" aria-label="Schedule type for ${htmlEsc(item.name)}">
          <option value="">Type required</option>
          <option value="wip" ${item.scheduleType==="wip"?"selected":""}>WIP</option>
          <option value="cc" ${item.scheduleType==="cc"?"selected":""}>Completed contracts</option>
          <option value="mixed" ${item.scheduleType==="mixed"?"selected":""}>WIP + completed contracts</option>
        </select></div>
    </div>
  </article>`;
}
function reviewBatchItem(i,view="certificate",batchAnalysis=false){
  if(!BATCH_ITEMS[i]||BATCH_ITEMS[i].status!=="ready")return;
  if(APP_STATE.batch.activeItem!==i)saveActiveBatchReview();
  APP_STATE.batch.analysisMode=batchAnalysis;
  APP_STATE.batch.activeItem=i;
  setSourceFile(BATCH_ITEMS[i].file);
  const saved=BATCH_ITEMS[i].reviewState;
  render(BATCH_ITEMS[i].doc,saved?{...saved,view}:{view});
  if(BATCH_MODE){
    if(batchMetadataReady()&&!APP_STATE.batch.matchState)buildMatchState();
    wireFlowNav();setFlowNav(view==="dash"?"timeseries":"batch");
  }
}
function renderBatchItemAnalysis(i){
  const item=BATCH_ITEMS[i];
  if(item?.periodEnd)renderPeriodAnalysis(item.periodEnd);
}
function analysisSwitcherHTML(){
  const periods=[...new Set(BATCH_ITEMS.filter(x=>x.status==="ready"&&x.periodEnd)
    .map(x=>x.periodEnd))].sort((a,b)=>a.localeCompare(b));
  if(!periods.length)return"";
  return`<nav class="analysis-switcher" aria-label="Analysis views"><span>Analysis view</span>
    <button class="btn analysis-portfolio-switch ${APP_STATE.batch.analysisScope==="portfolio"?"on":""}">Full portfolio</button>
    ${periods.map(period=>`<button class="btn analysis-period-switch ${APP_STATE.batch.analysisScope===period?"on":""}" data-period="${period}">
      ${new Date(period+"T00:00:00Z").toLocaleDateString("en-US",{month:"2-digit",day:"2-digit",year:"numeric",timeZone:"UTC"})}</button>`).join("")}</nav>`;
}

