/* v3 doc report -> flat list of v2-shaped section reports, each augmented
   with what the document layer proved about it. Section reports are
   v2-shaped by design, so renderCertificate/renderDash work untouched. */
function adaptV3(doc){
  const secs=[];
  const disc=(((doc.document||{}).concordance)||{}).discordant||[];
  for(const [tableIndex,t] of (doc.tables||[]).entries()){
    /* doc-level grand-totals verdict, explained against pooled findings */
    let dtd=null;
    const tc=t.totals_check;
    if(tc){
      const allF=(t.sections||[]).flatMap(x=>((x.report||{}).findings)||[]);
      const mism=[];
      for(const[j,c]of Object.entries(tc.columns||{})){
        if(c.matches)continue;
        const mcol=(t.numeric_col_map||[]).indexOf(+j);
        const header=(t.headers||[])[+j]||("column "+j);
        const explained=allF.some(f=>f.culprit_column===mcol
          &&f.proposed_correction!=null
          &&Math.abs(Math.abs(f.observed-f.proposed_correction)-Math.abs(c.difference))
            <=Math.max(2,.01*Math.abs(c.difference)));
        mism.push({header,stated:c.stated,computed:c.computed,diff:c.difference,explained});
      }
      dtd={present:true,allMatch:tc.all_match,mismatches:mism,
        allExplained:mism.length>0&&mism.every(m=>m.explained),docLevel:true};
    }
    for(const [sectionIndex,sec] of (t.sections||[]).entries()){
      const rep=sec.report||{};
      const rawValidation=sec.validation||sec.validator||rep.validation||null;
      if(!rep.totals&&Array.isArray(rawValidation?.totals))rep.totals=rawValidation.totals;
      if(!rep.totals_scope&&rawValidation?.totals_scope)rep.totals_scope=rawValidation.totals_scope;
      rep.metrics=doc.metrics||rep.metrics;
      rep._table_totals_check=tc||null;
      rep._table_numeric_col_map=t.numeric_col_map||[];
      rep._table_headers=t.headers||[];
      rep._doc_totals_detail=dtd;
      rep._extra=[];
      for(const mf of (t.misalignment_findings||[])){
        rep._extra.push({st:"warn",
          label:"Page "+(mf.pages||[]).join(", ")+" was read with a column offset \u2014 repaired and re-certified",
          note:"Every row on that page was transcribed out of alignment. The identities certified a deterministic repair: one structural finding, not one per cell. Any unrecoverable cells are shown as n/a."});
      }
      for(const iss of (t.stitch_issues||[])){
        if(iss.kind==="hjoin_missing_row")rep._extra.push({st:"bad",
          label:"Row '"+(iss.row_label||"?")+"' missing from the continuation page",
          note:"Present on the base page, absent from page "+(iss.page||"?")+". Its continuation columns are blank."});
        else if(iss.kind==="overlap_mismatch")rep._extra.push({st:"warn",
          label:"The same row was extracted twice with different values",
          note:"'"+(iss.row_label||"?")+"' disagrees between overlapping reads \u2014 extraction is unreliable there."});
        else if(iss.kind==="hjoin_rowcount_mismatch")rep._extra.push({st:"warn",
          label:"Facing pages carry different row counts",note:iss.note||""});
      }
      rep._headerComparison=buildHeaderComparison(t,rep,disc);
      /* CC: synthesize the corrections array the reviewer UI drives */
      const a=rep.analysis||{};
      if(a.schema==="cc"&&!(a.corrections||[]).length){
        a.corrections=(rep.findings||[])
          .filter(f=>f.proposed_correction!=null&&f.culprit_column!=null)
          .map(f=>({label:f.row_label,row:f.row_index,col:f.culprit_column,
            printed:f.observed,implied:f.proposed_correction,
            classification:f.classification,
            classification_label:f.classification_label,
            basis:f.correction_basis||[],
            checks:(f.failing_relations||[]).length,corroborated:false,variable:null}));
        rep.analysis=a;
      }
      /* CC: server signals -> the shape the dash renders */
      if(a.schema==="cc"&&(a.signals||[]).length&&!a.signals[0].headline){
        const by={};
        for(const g of a.signals)(by[g.signal]=by[g.signal]||[]).push(g);
        const META={loss_on_completed_contract:["completed at a loss",
            "A job that closed underwater. Realized, not estimated \u2014 it already hit the indemnitor's net worth."],
          profit_fade_on_completed_work:["profit given back on closed work",
            "Profit recognized in prior years reversed in the current year: warranty, claims, or late costs on finished jobs. The classic shape of estimates that were too optimistic."]};
        a.signals=Object.entries(by).map(([k,gs])=>({
          severity:Math.max(...gs.map(g=>g.severity||0)),
          headline:gs.length+" job"+(gs.length>1?"s":"")+" \u2014 "+(META[k]||[k])[0],
          jobs:gs.slice(0,5).map(g=>({label:g.job,detail:g.detail})),
          why:(META[k]||["",""])[1]}));
      }
      secs.push({type:sec.type,pages:sec.pages||t.pages||[],rep,
        tableIndex,sectionIndex,tablePages:t.pages||[]});
    }
  }
  return secs;
}
function secLabel(sc){
  const p=(sc.pages||[]).filter(x=>x!=null);
  const pageText=p.length?(p[0]===p[p.length-1]?"Page "+p[0]:"Pages "+p[0]+"–"+p[p.length-1]):"Section";
  const jobs=tableJobCount(sc.rep?.table);
  return {name:sc.type==="cc"?"Completed contracts":"WIP schedule",
    meta:pageText+(jobs?` · ${jobs} job${jobs===1?"":"s"}`:"")};
}
function renderSecnav(){
  const nav=$("#secnav");
  if(APP_STATE.document.view!=="certificate"||APP_STATE.document.sections.length<2){nav.classList.add("hidden");return;}
  nav.classList.add("secnav");nav.classList.remove("hidden");
  nav.innerHTML='<span class="secnav-title">Validation pages</span>'+APP_STATE.document.sections.map((sc,i)=>{
    const l=secLabel(sc),state=APP_STATE.document.correctionsBySection[i]||defaultAcceptedCorrections(sc.rep||{});
    const counts=computeValidationChecks(sc.rep||{},state),review=counts.nBad>0||counts.nFixed>0;
    return `<button class="${i===APP_STATE.document.activeSection?"on ":""}${review?"review":"clean"}" data-sec="${i}">
      <span class="sec-dot">${counts.nBad?"!":"✓"}</span><span class="sec-copy">
      <span class="sec-name">${l.name}</span><span class="sec-meta">${l.meta}</span></span></button>`;
  }).join("");
  nav.querySelectorAll("button").forEach(b=>b.onclick=()=>selectSection(+b.dataset.sec));
}
function selectSection(i){
  activateDocumentSection(i);
  renderSecnav();renderCertificate(APP_STATE.document.report);show("certificate");
  if(!BATCH_MODE)setSingleNav("validation");
  window.scrollTo(0,0);
}
function syncActiveSectionReview(){
  if(APP_STATE.document.activeSection>=0&&APP_STATE.document.correctionsBySection[APP_STATE.document.activeSection])APP_STATE.document.correctionsBySection[APP_STATE.document.activeSection]=new Set(APP_STATE.document.accepted);
}
function sectionVariableSignature(sc){
  return [...new Set((sc?.rep?.table?.columns||[]).map(c=>c.variable).filter(Boolean))]
    .sort().join("|");
}
function sectionPageRange(sc){
  const p=(sc?.pages||[]).map(Number).filter(Number.isFinite).sort((a,b)=>a-b);
  return p.length?[p[0],p[p.length-1]]:null;
}
function currentScheduleSectionIndexes(){
  const active=APP_STATE.document.sections[APP_STATE.document.activeSection];
  if(!active||active.type!=="wip")return APP_STATE.document.activeSection>=0?[APP_STATE.document.activeSection]:[];
  const sameTable=APP_STATE.document.sections.map((sc,i)=>({sc,i})).filter(x=>
    x.sc.type==="wip"&&active.tableIndex!=null&&x.sc.tableIndex===active.tableIndex).map(x=>x.i);
  if(sameTable.length>1)return sameTable;

  /* Conservative fallback for reports where each continuation page was emitted
     as its own logical table: combine only adjacent WIP pages with the same
     validated variable signature. Separate schedules remain separate. */
  const sig=sectionVariableSignature(active),candidates=APP_STATE.document.sections.map((sc,i)=>({sc,i,range:sectionPageRange(sc)}))
    .filter(x=>x.sc.type==="wip"&&x.range&&sectionVariableSignature(x.sc)===sig)
    .sort((a,b)=>a.range[0]-b.range[0]);
  const pos=candidates.findIndex(x=>x.i===APP_STATE.document.activeSection);
  if(pos<0)return[APP_STATE.document.activeSection];
  let lo=pos,hi=pos;
  while(lo>0&&candidates[lo].range[0]<=candidates[lo-1].range[1]+1)lo--;
  while(hi<candidates.length-1&&candidates[hi+1].range[0]<=candidates[hi].range[1]+1)hi++;
  return candidates.slice(lo,hi+1).map(x=>x.i);
}
function combinedWipReport(indexes){
  syncActiveSectionReview();
  const ordered=[...indexes].sort((a,b)=>a-b);
  if(ordered.length<=1)return APP_STATE.document.sections[ordered[0]]?.rep||APP_STATE.document.report;
  const rows=[],ids=[],names=[],labels=[],pages=[];
  const reps=ordered.map(i=>APP_STATE.document.sections[i]?.rep||{});
  ordered.forEach(i=>{
    const sc=APP_STATE.document.sections[i],rep=sc.rep||{},accepted=APP_STATE.document.correctionsBySection[i]||new Set();
    pages.push(...(sc.pages||[]));
    for(let row=0;row<tableJobCount(rep.table);row++){
      const identity=tableIdentity(rep.table,row),vars=canonicalVars(rep,row,accepted);
      rows.push(vars);ids.push(identity.jobId||"");names.push(identity.jobName||"");
      labels.push(identity.jobName||identity.jobId||identity.label||`Row ${rows.length}`);
    }
  });
  const columns=PRINT_COLUMN_ORDER.filter(variable=>rows.some(r=>Number.isFinite(+r[variable])))
    .map(variable=>({variable,header:PRINT_COLUMN_NAMES[variable]||variableName(variable),
      variable_name:PRINT_COLUMN_NAMES[variable]||variableName(variable)}));
  const uniquePages=[...new Set(pages.map(Number).filter(Number.isFinite))].sort((a,b)=>a-b);
  const pageLabel=uniquePages.length?(uniquePages[0]===uniquePages[uniquePages.length-1]
    ?`page ${uniquePages[0]}`:`pages ${uniquePages[0]}–${uniquePages[uniquePages.length-1]}`):`${ordered.length} sections`;
  const degraded=reps.some(r=>["header_mapped_unverified","llm_mapped_unverified","user_mapped_unverified","unmapped"].includes(r.overall_status));
  const tuning=reps.map(r=>r.analysis?.tuning).find(Boolean)||{};
  const jobs=rows.map((vars,i)=>({label:labels[i],...vars}));
  return{
    source:(reps[0]?.source||"WIP schedule")+` · combined ${pageLabel}`,
    overall_status:degraded
      ?(reps.some(r=>r.overall_status==="user_mapped_unverified")
        ?"user_mapped_unverified"
        :reps.some(r=>["header_mapped_unverified","llm_mapped_unverified"].includes(r.overall_status))
          ?"header_mapped_unverified":"unmapped")
      :"verified",
    validator_status:reps.some(r=>r.validator_status==="validation_failed")?"validation_failed":"success",
    findings:[],witnesses:[],metrics:APP_STATE.document.source?.metrics||reps[0]?.metrics||{},
    _combinedSectionCount:ordered.length,_combinedPages:uniquePages,_combinedPageLabel:pageLabel,
    table:{columns,values:rows.map(r=>columns.map(c=>r[c.variable]??null)),
      job_ids:ids,job_names:names,job_labels:labels},
    analysis:{schema:"wip",jobs,corrections:[],tuning,coverage:{
      numeric_cols:columns.length,mapped_cols:columns.length}}
  };
}
function documentAnalysisReport(){
  return combinedWipReport(currentScheduleSectionIndexes());
}
function renderDocumentAnalysis(){
  syncActiveSectionReview();APP_STATE.document.view="dash";renderSecnav();
  renderDash(documentAnalysisReport());show("dash");
  if(!BATCH_MODE)setSingleNav("analysis");
  window.scrollTo(0,0);
}

const COLUMN_MAPPING_GROUPS=[
  {label:"Profitability",variables:["V","C","G"],
   missing:"Any 2 · contract value, est. cost, est. profit"},
  {label:"Progress",variables:["D","Q","E"],
   missing:"Cost to date, cost to complete, or earned revenue"},
  {label:"Billing",variables:["B","N","U","O"],
   missing:"Billings, net position, or under + over"}
];
const COLUMN_MAPPING_OPTIONS=[
  {label:"Profitability",variables:["V","C","G","M"]},
  {label:"Progress",variables:["D","Q","E","H","P"]},
  {label:"Billing",variables:["B","N","U","O","PB"]},
  {label:"Remaining work",variables:["R","RB"]}
];
const SPARSE_MAPPING_STATUSES=new Set([
  "unmapped","header_mapped_unverified","llm_mapped_unverified"
]);

function columnMappingOptions(selected){
  const used=new Set();
  return`<option value="">Unmapped</option>${COLUMN_MAPPING_OPTIONS.map(group=>{
    const options=group.variables.filter(variable=>!used.has(variable)).map(variable=>{
      used.add(variable);
      const suffix=variable==="P"?" · reference only":"";
      return`<option value="${variable}" ${selected===variable?"selected":""}>${htmlEsc(variableName(variable)+suffix)}</option>`;
    }).join("");
    return`<optgroup label="${htmlEsc(group.label)}">${options}</optgroup>`;
  }).join("")}`;
}

function columnMappingSource(rep){
  const raw=rep.source_table||{};
  if(Array.isArray(raw.headers)&&Array.isArray(raw.rows)&&raw.headers.length){
    const numeric=(rep.parse?.numeric_col_map||[]).map(Number);
    return{headers:raw.headers.map(String),rows:raw.rows.map(row=>row.map(String)),
      matrixByDocument:new Map(numeric.map((docColumn,matrixColumn)=>[docColumn,matrixColumn]))};
  }
  const table=rep.table||{},headers=(table.columns||[]).map(c=>c.header||c.name||"");
  const rows=tableValues(table).map(row=>row.map(value=>value==null?"":String(value)));
  return{headers,rows,matrixByDocument:new Map(headers.map((_,index)=>[index,index]))};
}

function columnMappingState(rep){
  if(COLUMN_MAPPING_STATE.has(rep))return COLUMN_MAPPING_STATE.get(rep);
  const mapping={};
  (rep.table?.columns||[]).forEach((column,matrixColumn)=>{
    if(column?.variable)mapping[matrixColumn]=column.variable;
  });
  const state={mapping,suggested:{...mapping},inferred:{},touched:new Set(),conflict:""};
  COLUMN_MAPPING_STATE.set(rep,state);
  return state;
}

function columnMappingSelectClass(state,matrixColumn){
  const variable=state.mapping[matrixColumn];
  if(!variable)return state.inferred[matrixColumn]
    ?"mapping-select inferred":"mapping-select unmapped";
  return state.touched.has(matrixColumn)
    ?"mapping-select user":"mapping-select suggested";
}

function refreshColumnMappingInferences(rep,state){
  state.inferred=inferCorroboratingColumns(
    tableValues(rep.table),state.mapping,state.touched);
}

function updateColumnMappingControls(state){
  $("#mapping").querySelectorAll(".mapping-select").forEach(select=>{
    const matrixColumn=+select.dataset.mcol;
    const inferred=state.inferred[matrixColumn];
    select.value=state.mapping[matrixColumn]||inferred?.variable||"";
    select.className=columnMappingSelectClass(state,matrixColumn);
    const note=select.nextElementSibling;
    if(note?.classList.contains("mapping-validation-note"))
      note.textContent=inferred?.reason||"";
  });
}

function columnMappingRailHTML(state){
  const mapped=[...new Set(Object.values(state.mapping).filter(Boolean))];
  const ready=mappingReadiness(mapped);
  const missing=ready.groups.find(group=>!group.complete)?.id;
  let title="Map the minimum, not every column.";
  let copy="The three groups below are enough to calculate every core WIP value.";
  if(ready.score===1){
    title="Two input groups left.";
    copy="Keep mapping only what the calculation needs.";
  }else if(ready.score===2){
    title=missing==="billing"?"All we need is billing."
      :missing==="progress"?"All we need is progress."
      :"All we need is profitability.";
    copy="One more input group completes the WIP calculation.";
  }else if(ready.complete){
    title="Complete WIP calculation available.";
    copy=Object.keys(state.inferred).length
      ?"Additional matching columns are shown in blue."
      :"You can stop mapping here or include optional columns.";
  }
  const groupRows=COLUMN_MAPPING_GROUPS.map((definition,index)=>{
    const status=ready.groups[index],names=status.variables.map(variableName);
    return`<div class="mapping-group ${status.complete?"done":""}">
      <span class="mapping-group-mark">${status.complete?"\u2713":index+1}</span>
      <span><strong>${definition.label}</strong>
        <small>${status.complete?htmlEsc(names.join(" + ")):htmlEsc(definition.missing)}</small></span>
    </div>`;
  }).join("");
  const derived=Math.max(0,Object.keys(VARIABLE_NAMES).length-mapped.length);
  return`<div class="mapping-score"><strong>${ready.score} / ${ready.total}</strong><span>calculation inputs</span></div>
    <div class="mapping-meter" aria-label="${ready.score} of ${ready.total} calculation input groups complete">
      ${ready.groups.map(group=>`<span class="${group.complete?"on":""}"></span>`).join("")}</div>
    <h3>${title}</h3><p class="mapping-rail-copy">${copy}</p>
    <div class="mapping-groups">${groupRows}</div>
    <p class="mapping-conflict">${htmlEsc(state.conflict||"")}</p>
    <button class="btn primary" id="mappingAnalyze" ${ready.complete?"":"disabled"}>Analyze schedule</button>
    ${ready.complete?`<p class="mapping-derived">${mapped.length} mapped · up to ${derived} calculated</p>`:""}`;
}

function updateColumnMappingRail(rep,state){
  refreshColumnMappingInferences(rep,state);
  updateColumnMappingControls(state);
  const rail=$("#mappingRail");
  if(!rail)return;
  rail.innerHTML=columnMappingRailHTML(state);
  const analyze=$("#mappingAnalyze");
  if(analyze)analyze.onclick=()=>{
    applyColumnMapping(rep,state);
    const savedStates=APP_STATE.document.correctionsBySection.map(sectionState=>new Set(sectionState));
    render(APP_STATE.document.source,{states:savedStates,active:APP_STATE.document.activeSection,view:"dash"});
  };
}

function applyColumnMapping(rep,state){
  const mapping={...state.mapping};
  const displayMapping={...mapping};
  Object.entries(state.inferred).forEach(([matrixColumn,match])=>{
    displayMapping[matrixColumn]=match.variable;
  });
  (rep.table?.columns||[]).forEach((column,matrixColumn)=>{
    const variable=displayMapping[matrixColumn]||null;
    column.variable=variable;
    column.variable_name=variable?variableName(variable):null;
  });
  (rep.columns||[]).forEach((column,matrixColumn)=>{
    const variable=displayMapping[matrixColumn]||null;
    column.variable=variable;
    column.variable_name=variable?variableName(variable):null;
    column.provenance=variable
      ?(state.inferred[matrixColumn]?"math-corroborated"
        :state.touched.has(matrixColumn)?"user-confirmed":"header-matched")
      :"unassigned";
  });
  const values=tableValues(rep.table),labels=tableLabels(rep.table);
  const jobs=values.map((row,rowIndex)=>{
    const printed={};
    Object.entries(mapping).forEach(([matrixColumn,variable])=>{
      const value=row[+matrixColumn];
      if(variable&&value!==null&&value!==""&&Number.isFinite(+value))
        printed[variable]=+value;
    });
    return{label:labels[rowIndex]||`Row ${rowIndex+1}`,...deriveCanonicalVars(printed)};
  });
  rep.analysis={...(rep.analysis||{}),schema:"wip",basis:"user-mapped",
    jobs,corrections:[],signals:[],kpis:null,
    coverage:{...(rep.analysis?.coverage||{}),
      mapped_cols:Object.keys(displayMapping).length,
      numeric_cols:(rep.table?.columns||[]).length}};
  rep.overall_status="user_mapped_unverified";
  rep.fallback_notes="Column mapping was reviewed before analysis.";
  rep._headerComparison=buildHeaderComparison(null,rep,[]);
}

function renderColumnMapping(sectionIndex){
  activateDocumentSection(sectionIndex,{saveCurrent:false,view:APP_STATE.document.view});
  const rep=APP_STATE.document.report,state=columnMappingState(rep),source=columnMappingSource(rep);
  refreshColumnMappingInferences(rep,state);
  const section=APP_STATE.document.sections[APP_STATE.document.activeSection],sectionNote=APP_STATE.document.sections.length>1
    ?` · ${secLabel(section).name} · ${secLabel(section).pg}`:"";
  const header=source.headers.map((documentHeader,documentColumn)=>{
    const matrixColumn=source.matrixByDocument.get(documentColumn);
    const inferred=matrixColumn==null?null:state.inferred[matrixColumn];
    const selected=matrixColumn==null?"":state.mapping[matrixColumn]||inferred?.variable||"";
    const control=matrixColumn==null
      ?`<span class="mapping-reference">Reference column</span>`
      :`<select class="${columnMappingSelectClass(state,matrixColumn)}" data-mcol="${matrixColumn}"
          aria-label="Map ${htmlEsc(documentHeader||`column ${documentColumn+1}`)}">
          ${columnMappingOptions(selected)}</select>
        <small class="mapping-validation-note">${htmlEsc(inferred?.reason||"")}</small>`;
    return`<th><span class="mapping-doc-head">${htmlEsc(documentHeader||`Column ${documentColumn+1}`)}</span>${control}</th>`;
  }).join("");
  const body=source.rows.map(row=>`<tr>${source.headers.map((_,column)=>
    `<td>${htmlEsc(row[column]??"")}</td>`).join("")}</tr>`).join("");
  $("#mapping").innerHTML=`<div class="mapping-wide">
    <div class="mapping-head"><div><h2>Map the extracted columns</h2>
      <p>Suggested mappings are shaded. Review any column or continue when the calculation is complete${sectionNote}.</p></div></div>
    <div class="mapping-layout">
      <section class="mapping-table-card">
        <div class="mapping-table-note"><span>Extracted schedule</span><span>Every printed row remains visible</span></div>
        <div class="mapping-scroll"><table class="mapping-grid"><thead><tr>${header}</tr></thead>
          <tbody>${body}</tbody></table></div>
      </section>
      <aside class="mapping-rail" id="mappingRail">${columnMappingRailHTML(state)}</aside>
    </div></div>`;
  show("mapping");$("#secnav").classList.add("hidden");
  const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  for(const id of["navMatch","navConsolidated","navTrends","navCert","navDash"])
    $("#"+id).classList.add("hidden");
  if(BATCH_MODE){
    $("#navBatch").classList.remove("hidden");$("#navBatch").onclick=renderBatch;
  }else $("#navBatch").classList.add("hidden");
  $("#mapping").querySelectorAll(".mapping-select").forEach(select=>{
    select.onchange=()=>{
      const matrixColumn=+select.dataset.mcol;
      const previous=state.mapping[matrixColumn]||state.inferred[matrixColumn]?.variable||"";
      const variable=select.value;
      const duplicate=variable&&Object.entries(state.mapping)
        .find(([column,current])=>+column!==matrixColumn&&current===variable);
      if(duplicate){
        const otherHeader=rep.table?.columns?.[+duplicate[0]]?.header||`column ${+duplicate[0]+1}`;
        state.conflict=`${variableName(variable)} is already mapped to “${otherHeader}”. Clear it there first.`;
        select.value=previous;
      }else{
        if(variable)state.mapping[matrixColumn]=variable;else delete state.mapping[matrixColumn];
        state.touched.add(matrixColumn);state.conflict="";
      }
      updateColumnMappingRail(rep,state);
    };
  });
  updateColumnMappingRail(rep,state);
  window.scrollTo(0,0);
}

function render(doc,restore=null){
  if(doc.overall_status==="pipeline_error"){
    $("#errmsg").textContent="The pipeline hit an unexpected error: "+(doc.validator_reason||"unknown")+". Try again, or try the sample schedule.";
    show("err");return;
  }
  if(doc.overall_status==="extraction_failed"){
    $("#errmsg").textContent="The document could not be transcribed. If you're running without a model API key, try the sample schedule instead.";
    show("err");return;
  }
  const sections=doc.tables?adaptV3(doc):[{type:"wip",pages:[],rep:doc}];
  sections.forEach(section=>{
    const rep=section.rep||{};
    if(!rep._headerComparison)rep._headerComparison=buildHeaderComparison(null,rep,[]);
  });
  if(!sections.length){
    $("#errmsg").textContent="No tables were found in that document.";
    show("err");return;
  }
  const defaults=sections.map(sc=>defaultAcceptedCorrections(sc.rep||{}));
  const correctionsBySection=restore?.states?.length===sections.length
    ?restore.states.map(s=>new Set(s)):defaults;
  const activeSection=Math.max(0,Math.min(restore?.active||0,sections.length-1));
  const view=restore?.view==="dash"?"dash":"certificate";
  initializeDocumentState({source:doc,sections,correctionsBySection,activeSection,view});
  const sparseSection=APP_STATE.document.sections.findIndex(section=>
    section.type==="wip"&&SPARSE_MAPPING_STATUSES.has(section.rep?.overall_status));
  if(sparseSection>=0){
    renderColumnMapping(sparseSection);
    return;
  }
  renderSecnav();
  if(APP_STATE.document.view==="dash"){renderDash(documentAnalysisReport());show("dash");}
  else{renderCertificate(APP_STATE.document.report);show("certificate");}
  const nav=$("#nav");nav.classList.remove("hidden");$("#tagline").classList.add("hidden");
  if(BATCH_MODE){
    $("#navBatch").classList.remove("hidden");
    $("#navMatch").classList.add("hidden");$("#navConsolidated").classList.add("hidden");
    $("#navTrends").classList.add("hidden");
    $("#navCert").classList.remove("hidden");$("#navDash").classList.remove("hidden");
    $("#navBatch").onclick=renderBatch;
  }else{
    setSingleNav(APP_STATE.document.view==="dash"?"analysis":"validation");
    $("#navConsolidated").onclick=renderSingleValidatedWip;
  }
  $("#navCert").onclick=()=>{syncActiveSectionReview();activateDocumentSection(APP_STATE.document.activeSection,{saveCurrent:false});renderSecnav();renderCertificate(APP_STATE.document.report);show("certificate");if(!BATCH_MODE)setSingleNav("validation");window.scrollTo(0,0)};
  $("#navDash").onclick=renderDocumentAnalysis;
}

