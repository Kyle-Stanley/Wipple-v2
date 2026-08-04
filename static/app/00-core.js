const API="";  /* serving this file from Firebase/elsewhere? set to your Railway URL, e.g. "https://yourapp.up.railway.app" (no trailing slash) */
const $=s=>document.querySelector(s);
const show=id=>{for(const s of["landing","processing","certificate","dash","batch","matching","mapping","consolidated","timeseries","err"])
  $("#"+s).classList.toggle("hidden",s!==id);
  // The landing carries its own lockup, so the header mark is redundant there.
  // Hidden rather than removed: the header is space-between, and dropping the
  // mark from the flow would pull the tagline over to the left.
  $("header .mark").style.visibility=id==="landing"?"hidden":""};
const fmtM=x=>{if(x==null)return"—";const a=Math.abs(x),
  s=a>=1e6?(a/1e6).toFixed(a>=1e7?1:2)+"M":a>=1e3?(a/1e3).toFixed(0)+"k":a.toFixed(0);
  return x<0?`($${s})`:`$${s}`};
const fmt$=x=>x==null?"—":(x<0?`(${Math.abs(x).toLocaleString(undefined,{maximumFractionDigits:0})})`
  :x.toLocaleString(undefined,{maximumFractionDigits:0}));

/* Internal formula codes stay compact in the math display, but bare codes
   should never be shown as user-facing column names. */
const VARIABLE_NAMES={
  V:"Contract Value",
  C:"Estimated Total Cost",
  G:"Estimated Gross Profit",
  M:"Estimated Gross Margin %",
  D:"Cost to Date",
  Q:"Cost to Complete",
  P:"Percent Complete",
  E:"Earned Revenue",
  B:"Billings to Date",
  H:"Earned Gross Profit to Date",
  N:"Net Billing Position",
  U:"Underbillings",
  O:"Overbillings",
  R:"Remaining Revenue (Backlog)",
  RB:"Remaining Billings",
  PB:"Percent Billed"
};
const variableName=code=>VARIABLE_NAMES[code]||code||"Detected variable";

/* Header text is informational only. Numeric validation remains completely
   header-blind; this registry is used only to recognize familiar terminology
   and diagnose a clear one-column header association offset in the UI. */
const HEADER_VARIANT_TEXT={
  V:[
    "Contract Value","Total Contract Value","Contract Amount","Total Contract Amount",
    "Contract Amt","Contract Price","Total Contract Price","Adjusted Contract Price",
    "Adjusted Contract Amount","Revised Contract Price","Revised Contract Amount",
    "Current Contract Price","Current Contract Value","Total Estimated Contract",
    "Estimated Revenue","Estimated Total Revenue","Revenues","Contract Revenue",
    "Total Contract Revenue","Contract Amount Including Change Orders",
    "Contract Amount Incl. Change Orders","Contract Price Including Change Orders",
    "Contract Price Incl. Approved Change Orders","Contract Price Including Approved Change Orders",
    "Total Contracts","Contract","Original Contract Plus Change Orders",
    "Original Contract Amount Plus Change Orders","Revised Contract Sum","Contract Sum"
  ],
  C:[
    "Estimated Cost","Estimated Costs","Total Estimated Cost","Total Estimated Costs",
    "Estimated Total Cost","Estimated Total Costs","Anticipated Contract Cost",
    "Anticipated Contract Costs","Direct Contract Cost","Direct Contract Costs",
    "Estimated Contract Cost","Estimated Contract Costs","Total Contract Cost",
    "Total Contract Costs","Projected Total Cost","Forecast Total Cost",
    "Forecasted Total Cost","Estimated Cost at Completion","Cost at Completion",
    "Revised Estimated Cost","Revised Estimated Costs","Latest Estimated Cost"
  ],
  G:[
    "Estimated Gross Profit","Total Estimated Gross Profit","Total Est Gross Profit",
    "Est Gross Profit","Estimated Profit","Total Estimated Profit","Total Profit",
    "Projected Gross Profit","Projected Profit","Anticipated Gross Profit",
    "Forecast Gross Profit","Gross Profit Estimate","Estimated Contract Profit",
    "Contract Gross Profit","Total Contract Gross Profit","Gross Profit",
    "Original Estimate of Gross Profit Incl. Change Orders",
    "Original Estimate of Gross Profit Including Change Orders","Revised Estimate Gross Profit",
    "Revised Estimated Gross Profit","Revised Estimate of Gross Profit"
  ],
  M:[
    "Estimated Gross Margin %","Estimated Gross Margin","Estimated Gross Margin Percent",
    "Estimated Gross Margin Percentage","Gross Margin","Gross Margin %",
    "Gross Margin Percent","Gross Profit %","Gross Profit Percent","GP %",
    "GP Percent","Profit Margin","Estimated Profit Margin"
  ],
  D:[
    "Cost to Date","Costs to Date","Total Cost to Date","Total Costs to Date",
    "Cost Incurred to Date","Costs Incurred to Date","Total Cost Incurred to Date",
    "Total Costs Incurred to Date","Actual Cost to Date","Actual Costs to Date",
    "Actual Total Costs to Date","Actual Costs Incurred to Date",
    "Cost of Earned Revenue to Date","Cost of Earned Revenue","Cost of Revenues",
    "Cost of Revenue","Cost of Construction","Costs of Construction",
    "Recognized Costs","Earned Cost","Cumulative Costs","Inception to Date Costs"
  ],
  Q:[
    "Cost to Complete","Costs to Complete","Estimated Cost to Complete",
    "Estimated Costs to Complete","Estimate Cost to Complete","Estimate Costs to Complete",
    "Estimate Cost to Complete Remaining Work","Estimated Cost to Complete Remaining Work",
    "Estimated Cost of Remaining Work","Estimated Costs of Remaining Work",
    "Cost of Remaining Work","Remaining Estimated Cost","Remaining Estimated Costs",
    "Remaining Cost","Remaining Costs","Cost Remaining","Projected Cost to Complete",
    "Forecast Cost to Complete","Balance to Complete"
  ],
  P:[
    "Percent Complete","Percentage Complete","% Complete","Completion Percentage",
    "Percent Completed","Percentage Completed","Pct Complete","Pct Completed",
    "Current Percent Complete","Current Percentage Complete",
    "Current Percentage of Completion","Percentage of Completion","Percent of Completion",
    "Completion Percent"
  ],
  E:[
    "Earned Revenue","Earned Revenues","Revenue Earned","Revenues Earned",
    "Total Earned Revenue","Total Revenue Earned","Recognized Revenue",
    "Revenue Recognized","Recognized Revenues","Revenues Recognized",
    "Earned Revenue to Date","Revenue Earned to Date","Revenue to Date",
    "Cumulative Earned Revenue","Inception to Date Revenue","Contract Revenue Earned",
    "Contract Revenues Earned","Percentage of Completion Revenue"
  ],
  B:[
    "Billed to Date","Billings to Date","Total Billed to Date","Total Billings to Date",
    "Amount Billed to Date","Total Amount Billed to Date","Contract Billings to Date",
    "Total Contract Billings","Total Contract Billings to Date","Billings",
    "Cumulative Billings","Inception to Date Billings","Progress Billings",
    "Total Progress Billings","Total Contract Billings Including Retention",
    "Total Contract Billings Including Retainage",
    "Total Amount Billed to Date Incl. Retainage",
    "Total Amount Billed to Date Including Retainage","Billings Including Retainage"
  ],
  H:[
    "Earned Gross Profit to Date","Earned Gross Profit","Gross Profit Earned",
    "Gross Profit Earned to Date","Gross Profit Recognized","Recognized Gross Profit",
    "Gross Profit to Date","Cumulative Gross Profit","Earned Profit",
    "Earned Profit to Date","Profit Earned","Recognized Profit","Gross Profit"
  ],
  N:[
    "Net Billing Position","Net Billings Position","Billing Position","Billings Position",
    "Net Over Under Billing","Net Over Under Billings","Net Under Over Billing",
    "Net Under Over Billings","Underbilled Overbilled","Overbilled Underbilled",
    "Underbilled (Overbilled)","Overbilled (Underbilled)","Underbilling Overbilling",
    "Overbilling Underbilling","Contract Asset Liability","Net Contract Asset Liability"
  ],
  U:[
    "Underbillings","Underbilling","Under Billings","Under Billing","Underbilled",
    "Amount Underbilled","Costs and Estimated Earnings in Excess of Billings",
    "Cost and Estimated Earnings in Excess of Billings",
    "Cost and Est. Earnings in Excess of Billing","Cost and Est. Earnings in Excess of Billings",
    "Costs and Est Earnings in Excess of Billings","Costs and Earnings in Excess of Billings",
    "Costs in Excess of Billings","Earned Revenue in Excess of Billings",
    "Contract Asset","Contract Assets","Unbilled Revenue","Underbilled (Overbilled)"
  ],
  O:[
    "Overbillings","Overbilling","Over Billings","Over Billing","Overbilled",
    "Amount Overbilled","Billings in Excess of Costs and Estimated Earnings",
    "Billing in Excess of Costs and Estimated Earnings",
    "Billings in Excess of Cost and Estimated Earnings",
    "Billings in Excess of Costs and Est Earnings","Billings in Excess of Costs and Earnings",
    "Billings in Excess of Costs","Billings in Excess of Earned Revenue",
    "Contract Liability","Contract Liabilities","Deferred Revenue",
    "Advance Billings","Underbilled (Overbilled)"
  ],
  R:[
    "Remaining Revenue (Backlog)","Remaining Revenue","Revenue Remaining","Backlog",
    "Revenue Backlog","Remaining Contract Revenue","Contract Revenue Remaining",
    "Unearned Revenue","Revenue to Complete","Remaining Contract Value"
  ],
  RB:[
    "Remaining Billings","Billings Remaining","Remaining Amount to Bill",
    "Amount Remaining to Bill","Unbilled Contract Amount","Unbilled Contract Balance",
    "Remaining Contract Billings","Balance to Bill","Billing Balance","Future Billings"
  ],
  PB:[
    "Percent Billed","Percentage Billed","% Billed","Billing Percentage",
    "Billed Percent","Billed Percentage","Pct Billed","Percent of Contract Billed",
    "Percentage of Contract Billed","Billings Percent"
  ]
};
function normalizeHeader(value){
  return String(value||"").toLowerCase().replace(/&/g," and ").replace(/%/g," percent ")
    .replace(/[^a-z0-9]+/g," ").trim().replace(/\s+/g," ");
}
const HEADER_VARIANTS=Object.fromEntries(Object.entries(HEADER_VARIANT_TEXT).map(([variable,values])=>[
  variable,new Set([variableName(variable),...values].map(normalizeHeader))
]));
const headerMatches=(variable,header)=>HEADER_VARIANTS[variable]?.has(normalizeHeader(header))||false;

function buildHeaderComparison(table,rep,discordant=[]){
  const tableColumns=rep?.table?.columns||[];

  /* The report table already carries the two facts this reference view needs:
     `variable` / `variable_name` is what the numeric validator proved, while
     `header` is the document header attached to that same displayed column.
     Do not reinterpret the displayed-column index as a numeric-matrix index:
     derived/virtual columns can exist in the report without existing in
     numeric_col_map, which is what caused Billings to Date and every later
     header to slide in the first implementation. */
  let rows=tableColumns
    .filter(column=>column?.variable&&(column.header||column.name))
    .map((column,index)=>({
      columnIndex:index,
      variable:column.variable,
      expected:column.variable_name||variableName(column.variable),
      document:column.header||column.name||"—"
    }));

  let appliedOffset=0;
  if(rows.length){
    const documents=rows.map(row=>row.document);
    const scored=[-1,0,1].map(offset=>({
      offset,
      score:rows.reduce((sum,row,index)=>{
        const candidate=documents[index+offset];
        return sum+(candidate&&headerMatches(row.variable,candidate)?1:0);
      },0)
    }));
    const unshifted=scored.find(item=>item.offset===0);
    const bestScore=Math.max(...scored.map(item=>item.score));
    const best=scored.filter(item=>item.score===bestScore);
    if(best.length===1&&best[0].offset!==0&&best[0].score>=4
      &&best[0].score>=(unshifted?.score||0)+3){
      appliedOffset=best[0].offset;
      rows=rows.map((row,index)=>({
        ...row,
        document:documents[index+appliedOffset]||"—"
      }));
    }
  }

  /* Legacy fallback: older report shapes may expose only discordant terms. */
  if(!rows.length&&discordant.length){
    rows=discordant.map((item,index)=>({
      columnIndex:index,variable:item.variable,
      expected:item.name||variableName(item.variable),
      document:item.header||"—"
    }));
  }

  rows=rows.map(row=>({
    ...row,
    recognized:headerMatches(row.variable,row.document)
  }));

  return {rows,appliedOffset};
}

function headerComparisonHTML(rep){
  const comparison=rep?._headerComparison||buildHeaderComparison(null,rep,[]);
  if(!comparison.rows.length)return"";
  const baseNote=comparison.appliedOffset
    ?"The document headers were consistently offset by one column and were realigned for this display. Numeric validation was unchanged."
    :"Shown for reference only. Column identification and validation used the values, not the header text.";
  const note=baseNote+" Green cells use recognized terminology; yellow cells are not yet in the canonical header list.";
  const cellClass=row=>row.recognized?"header-known":"header-unknown";
  return `<section class="header-compare"><h3>Column headers</h3><p class="header-note">${note}</p>
    <div class="header-compare-scroll"><table class="header-compare-table"><tbody>
      <tr><th scope="row">Expected</th>${comparison.rows.map(row=>`<td class="${cellClass(row)}">${htmlEsc(row.expected)}</td>`).join("")}</tr>
      <tr><th scope="row">Document</th>${comparison.rows.map(row=>`<td class="${cellClass(row)}">${htmlEsc(row.document)}</td>`).join("")}</tr>
    </tbody></table></div></section>`;
}

const formulaHeading=formula=>{
  const m=String(formula||"").match(/^\s*([A-Z]{1,2})\s*=/);
  return m?variableName(m[1]):"Validation check";
};

const causeText=(cls,printed,implied,label)=>{
  if(cls==="separator_or_magnitude_error"&&printed&&implied){
    const dp=String(Math.round(Math.abs(printed))),di=String(Math.round(Math.abs(implied)));
    for(const k of[1,2,3]){
      const z="0".repeat(k);
      if(dp===di+z)return k>1?`${k} extra digits`:"extra digit";
      if(di===dp+z)return k>1?`${k} missing digits`:"missing digit";
    }
    if(dp===di)return"misread separator";
    return"extra or missing digit";
  }
  return label||CLASS_SHORT[cls]||cls;
};
const CLASS_SHORT={separator_or_magnitude_error:"extra or missing digit",
  ocr_character_misread:"digit error",digit_transposition:"digits swapped",
  extra_character:"extra digit",dropped_character:"missing digit",
  formatting_only:"formatting",unexplained_substitution:"unknown",
  ambiguous_multi_cell:"multiple cells"};
const checksText=c=>`${c.checks} identit${c.checks===1?"y":"ies"}${c.corroborated?" + totals row":""}`;
const CLASS_EN={separator_or_magnitude_error:"decimal or thousands-separator slip",
  ocr_character_misread:"one or more digits differ from the implied value",digit_transposition:"digits transposed",
  extra_character:"stray extra digit",dropped_character:"missing digit",
  formatting_only:"formatting discrepancy",
  ambiguous_multi_cell:"multiple errors in this row, no single cell isolated",
  unexplained_substitution:"no transcription pattern, the value appears genuinely wrong"};

$("#pick").onclick=()=>$("#file").click();
$("#file").onchange=e=>{
  const files=[...(e.target.files||[])];
  e.target.value="";
  if(files.length)scanFiles(files);
};
$("#sample").onclick=()=>{
  resetBatch();clearSourceFile();
  const model=$("#model")?.value||"";
  const qs=model?`?model=${encodeURIComponent(model)}`:"";
  stream(API+"/api/sample"+qs);
};
const dz=$("#drop");
["dragover","dragenter"].forEach(t=>dz.addEventListener(t,e=>{e.preventDefault();dz.classList.add("on")}));
["dragleave","drop"].forEach(t=>dz.addEventListener(t,e=>{e.preventDefault();dz.classList.remove("on")}));
dz.addEventListener("drop",e=>{
  const files=[...(e.dataTransfer.files||[])];
  if(files.length)scanFiles(files);
});

let RUNNING=false,DOTS=0,LATEST_PROGRESS=null;
function animatedLines(){
  // Batch runs narrate several documents at once: every in-flight lane keeps
  // its own ticking line. A single scan still animates its newest log line.
  if(!$("#batchLanes").classList.contains("hidden"))
    return [...document.querySelectorAll("#batchLanes .lane.processing .lane-msg")];
  return LATEST_PROGRESS?[LATEST_PROGRESS]:[];
}
setInterval(()=>{
  if(!RUNNING)return;
  DOTS=(DOTS+1)%4;
  for(const el of animatedLines()){
    const base=el.dataset.base||el.textContent.replace(/\.*$/,"");
    el.textContent=base+".".repeat(DOTS);
  }
},450);
function finishProgressLine(){
  for(const el of animatedLines())
    if(el.dataset.base)el.textContent=el.dataset.base;
}
function showBatchProgress(completed,total,running=0){
  $("#batchProgress").classList.remove("hidden");
  $("#batchProgressCount").textContent=`${completed} of ${total} complete`;
  $("#batchProgressFile").textContent=running?`${running} reading in parallel`:"";
  $("#batchProgressBar").style.width=`${total?100*completed/total:0}%`;
}
function resetProgressStage(){
  finishProgressLine();DOTS=0;LATEST_PROGRESS=null;$("#log").innerHTML="";
}
function laneIcon(status){
  return status==="ready"?"✓":status==="failed"?"!":status==="processing"?"·":"";
}
function renderBatchLanes(){
  const el=$("#batchLanes");
  el.innerHTML=BATCH_ITEMS.map((item,i)=>`<div class="lane ${item.status}" data-lane="${i}">
    <span class="lane-dot" aria-hidden="true">${laneIcon(item.status)}</span>
    <span class="lane-name" title="${htmlEsc(item.name)}">${htmlEsc(item.name)}</span>
    <span class="lane-msg"></span></div>`).join("");
  el.classList.remove("hidden");$("#log").classList.add("hidden");
  BATCH_ITEMS.forEach((_,i)=>updateBatchLane(i));
}
function updateBatchLane(i){
  const item=BATCH_ITEMS[i],row=$(`#batchLanes .lane[data-lane="${i}"]`);
  if(!row)return;
  row.className=`lane ${item.status}`;
  row.querySelector(".lane-dot").textContent=laneIcon(item.status);
  const msg=row.querySelector(".lane-msg"),base=String(item.line||"").replace(/\.*$/,"");
  msg.dataset.base=base;msg.textContent=base;
}
function hideBatchLanes(){
  $("#batchLanes").classList.add("hidden");$("#batchLanes").innerHTML="";
  $("#log").classList.remove("hidden");
}

let SOURCE_FILE=null,SOURCE_URL=null,SOURCE_KIND="";
function clearSourceFile(){
  if(SOURCE_URL)URL.revokeObjectURL(SOURCE_URL);
  SOURCE_FILE=null;SOURCE_URL=null;SOURCE_KIND="";
}
function setSourceFile(f){
  clearSourceFile();SOURCE_FILE=f;SOURCE_URL=URL.createObjectURL(f);
  const n=(f.name||"").toLowerCase();
  SOURCE_KIND=(f.type==="application/pdf"||n.endsWith(".pdf"))?"pdf":
    ((f.type||"").startsWith("image/")||/\.(png|jpe?g|webp|gif)$/.test(n))?"image":"other";
}
function canReviewSource(){return Boolean(SOURCE_URL&&SOURCE_KIND!=="other")}
window.addEventListener("beforeunload",clearSourceFile);

function scanFiles(files){
  if(files.length===1){resetBatch();scanFile(files[0]);return;}
  scanBatch(files);
}
function scanFile(f){setSourceFile(f);const fd=new FormData();fd.append("file",f);
  const m=$("#model");if(m&&m.value)fd.append("model",m.value);
  stream(API+"/api/scan",{method:"POST",body:fd})}

async function readStream(url,opts,onProgress=addLine){
  const r=await fetch(url,opts);
  if(!r.ok)throw new Error(`Upload failed (${r.status})`);
  if(!r.body)throw new Error("The server returned no processing stream.");
  const rd=r.body.getReader(),dec=new TextDecoder();let buf="",report=null;
  while(true){
    const{done,value}=await rd.read();if(done)break;
    buf+=dec.decode(value,{stream:true});
    let i;while((i=buf.indexOf("\n\n"))>=0){
      const block=buf.slice(0,i);buf=buf.slice(i+2);
      const ev=(block.match(/^event: (.+)$/m)||[])[1];
      const data=(block.match(/^data: (.+)$/m)||[])[1];
      if(!ev||!data)continue;
      if(ev==="progress")onProgress(JSON.parse(data).message);
      if(ev==="report")report=JSON.parse(data);
    }
  }
  if(!report)throw new Error("Processing ended without a report.");
  return report;
}
async function stream(url,opts){
  show("processing");$("#batchProgress").classList.add("hidden");hideBatchLanes();
  $("#log").innerHTML="";
  RUNNING=true;DOTS=0;LATEST_PROGRESS=null;
  const startedAt=performance.now();
  try{
    const doc=await readStream(url,opts);
    attachClientElapsed(doc,(performance.now()-startedAt)/1000);
    RUNNING=false;finishProgressLine();
    setTimeout(()=>render(doc),650);
  }catch(e){
    RUNNING=false;finishProgressLine();
    $("#errmsg").textContent=String(e);show("err");
  }
}
function addLine(m){
  finishProgressLine();DOTS=0;
  const d=document.createElement("div");d.className="ln";
  const bullet=document.createElement("span");bullet.className="dot";bullet.textContent="·";
  const txt=document.createElement("span");txt.dataset.base=String(m).replace(/\.*$/,"");txt.textContent=txt.dataset.base;
  d.appendChild(bullet);d.appendChild(txt);$("#log").appendChild(d);
  while($("#log").children.length>7)$("#log").firstElementChild.remove();
  LATEST_PROGRESS=txt;
}

const ADDITIVE_TOTAL_VARS=new Set(["V","C","G","D","Q","E","B","H","N","U","O","R","RB"]);
const MAGNITUDE_TOTAL_VARS=new Set(["U","O"]);
function backendTotals(rep){
  const candidates=[rep?.totals,rep?.validation?.totals,rep?.validator?.totals,
    rep?.analysis?.totals,rep?._validation?.totals];
  return candidates.find(Array.isArray)||null;
}
function backendTotalsScope(rep){
  return rep?.totals_scope||rep?.validation?.totals_scope||rep?.validator?.totals_scope
    ||rep?.analysis?.totals_scope||rep?._validation?.totals_scope||null;
}
function totalCorrectionKey(item){return `total:${item.column}:${item.variable||""}`;}
function totalDistance(a,b,variable){
  if(!Number.isFinite(+a)||!Number.isFinite(+b))return Infinity;
  return MAGNITUDE_TOTAL_VARS.has(variable)
    ?Math.abs(Math.abs(+a)-Math.abs(+b)):Math.abs(+a-(+b));
}
function presentTotal(value,stated,variable){
  if(!Number.isFinite(+value))return null;
  const n=+value;
  return MAGNITUDE_TOTAL_VARS.has(variable)&&+stated<0?-Math.abs(n):n;
}
function totalTolerance(stated,computed,nRows,provided=0){
  return Math.max(Number.isFinite(+provided)?+provided:0,1,.01*Math.max(nRows||1,1),
    1e-9*Math.max(Math.abs(+stated||0),Math.abs(+computed||0)));
}
function totalColumnMeta(rep,column,variable,legacyRawColumn=false){
  const cols=rep?.table?.columns||[];
  let displayIndex=-1;
  if(variable)displayIndex=cols.findIndex(c=>c?.variable===variable);
  if(displayIndex<0){
    const map=rep?.parse?.numeric_col_map||rep?._table_numeric_col_map||[];
    displayIndex=legacyRawColumn?map.indexOf(+column):+column;
  }
  const c=displayIndex>=0?cols[displayIndex]:null;
  const sourceHeader=rep?._table_headers?.[+column];
  return {displayIndex,column:c,variable:variable||c?.variable||null,
    header:c?.header||c?.variable_name||sourceHeader||variableName(variable||c?.variable)||`column ${column}`};
}
function semanticJobTotal(rep,variable,accepted){
  if(!variable)return null;
  const jobs=currentJobs(rep,accepted);
  if(!jobs.length)return null;
  const vals=jobs.map(job=>+job[variable]);
  if(vals.some(v=>!Number.isFinite(v)))return null;
  return vals.reduce((sum,v)=>sum+(MAGNITUDE_TOTAL_VARS.has(variable)?Math.abs(v):v),0);
}
function acceptedJobCorrections(rep,variable,accepted){
  return (rep?.analysis?.corrections||[]).map((c,i)=>({c,i}))
    .filter(({c,i})=>c.variable===variable&&accepted?.has(i));
}
function totalsScopeNote(td){
  if(!td)return"";
  const assessed=td.assessedCount||0,ignored=td.ignoredCount||0;
  if(!assessed&&!ignored)return"";
  return `${assessed} mapped additive column${assessed===1?" was":"s were"} assessed${ignored?`; ${ignored} other numeric column${ignored===1?" was":"s were"} outside this totals check`:""}.`;
}
function totalsDetail(rep,accepted=ACCEPTED){
  const nRows=tableJobCount(rep.table)||rep?.parse?.n_rows||0;
  const payload=backendTotals(rep);
  if(payload){
    const rows=[];
    for(const item of payload){
      const variable=item.variable||null;
      const meta=totalColumnMeta(rep,item.column,variable,false);
      const stated=+item.stated_total;
      const raw=+item.raw_row_sum;
      const backendValidated=+item.validated_row_sum;
      let computed=semanticJobTotal(rep,variable,accepted);
      if(!Number.isFinite(computed))computed=backendValidated;
      computed=presentTotal(computed,stated,variable);
      const tol=totalTolerance(stated,computed,nRows,item.tolerance);
      const rawAgrees=totalDistance(stated,raw,variable)<=tol;
      const computedAgrees=totalDistance(stated,computed,variable)<=tol;
      const appliedJobs=acceptedJobCorrections(rep,variable,accepted);
      const key=totalCorrectionKey(item);
      let status=item.status||"unassessed",explained=false,proposed=null;
      if(status==="unassessed"||!Number.isFinite(stated)||!Number.isFinite(computed)){
        status="unassessed";
      }else if(computedAgrees){
        status=appliedJobs.length||item.status==="pass_after_corrections"
          ?"pass_after_corrections":"pass";
        explained=true;
      }else if(rawAgrees&&appliedJobs.length){
        status="conflicts_with_job_corrections";
      }else{
        status="total_row_error";
        proposed=presentTotal(computed,stated,variable);
        if(accepted?.has(key)){status="total_row_corrected";explained=true;}
      }
      rows.push({header:meta.header,column:item.column,variable,stated,raw,
        computed,diff:stated-computed,status,explained,proposedCorrection:proposed,
        correctionKey:key,reason:item.reason||"",colFindings:(rep.findings||[])
          .filter(f=>f.culprit_variable===variable||f.culprit_column===item.column).length});
    }
    const nonPass=rows.filter(r=>r.status!=="pass");
    const totalCorrections=rows.filter(r=>r.proposedCorrection!=null)
      .map(r=>({...r,accepted:accepted?.has(r.correctionKey)}));
    const scope=backendTotalsScope(rep)||{};
    const numeric=rep?.parse?.n_numeric_cols??rep?.analysis?.coverage?.numeric_cols;
    const assessed=rows.length;
    const ignored=Number.isFinite(+numeric)?Math.max(0,+numeric-assessed)
      :Array.isArray(scope.unmapped_numeric_columns)?scope.unmapped_numeric_columns.length:0;
    return {present:rows.length>0,allMatch:nonPass.length===0,mismatches:nonPass,
      allExplained:nonPass.length>0&&nonPass.every(r=>r.explained),
      totalCorrections,assessedCount:assessed,ignoredCount:ignored,scope};
  }

  /* Legacy report path. The table-level totals check may cover every extracted
     numeric column, but only columns mapped to additive WIP variables belong in
     this validation. Period/supplemental columns remain intentionally ignored. */
  const tc=rep.totals_check||rep._table_totals_check;
  if(!tc)return rep._doc_totals_detail||null;
  const after=rep.analysis?.totals_after_corrections||{};
  const rows=[];
  for(const[j,c]of Object.entries(tc.columns||{})){
    const meta=totalColumnMeta(rep,j,null,true);
    const variable=meta.variable;
    if(meta.displayIndex<0||!ADDITIVE_TOTAL_VARS.has(variable))continue;
    const resolved=after[j]||after[+j]||null;
    const stated=+c.stated,raw=+c.computed;
    let computed=semanticJobTotal(rep,variable,accepted);
    if(!Number.isFinite(computed)&&resolved&&Number.isFinite(+resolved.computed_after_corrections))
      computed=+resolved.computed_after_corrections;
    if(!Number.isFinite(computed))computed=raw;
    computed=presentTotal(computed,stated,variable);
    const tol=totalTolerance(stated,computed,nRows,
      Math.max(2,.02*nRows,1e-6*Math.abs(stated||0)));
    const rawAgrees=totalDistance(stated,raw,variable)<=tol;
    const computedAgrees=totalDistance(stated,computed,variable)<=tol;
    const appliedJobs=acceptedJobCorrections(rep,variable,accepted);
    const base={column:meta.displayIndex,variable};
    const key=totalCorrectionKey(base);
    let status="pass",explained=true,proposed=null;
    if(computedAgrees){status=appliedJobs.length&&!rawAgrees?"pass_after_corrections":"pass";}
    else if(rawAgrees&&appliedJobs.length){status="conflicts_with_job_corrections";explained=false;}
    else{
      status="total_row_error";explained=false;proposed=presentTotal(computed,stated,variable);
      if(accepted?.has(key)){status="total_row_corrected";explained=true;}
    }
    rows.push({header:meta.header,column:meta.displayIndex,variable,stated,raw,
      computed,diff:stated-computed,status,explained,proposedCorrection:proposed,
      correctionKey:key,colFindings:(rep.findings||[])
        .filter(f=>f.culprit_column===meta.displayIndex).length});
  }
  const nonPass=rows.filter(r=>r.status!=="pass");
  const totalCorrections=rows.filter(r=>r.proposedCorrection!=null)
    .map(r=>({...r,accepted:accepted?.has(r.correctionKey)}));
  const numeric=rep?.parse?.n_numeric_cols??rep?.analysis?.coverage?.numeric_cols;
  return {present:rows.length>0,allMatch:nonPass.length===0,mismatches:nonPass,
    allExplained:nonPass.length>0&&nonPass.every(r=>r.explained),totalCorrections,
    assessedCount:rows.length,ignoredCount:Number.isFinite(+numeric)?Math.max(0,+numeric-rows.length):0};
}
function defaultAcceptedCorrections(rep){
  const accepted=new Set((rep?.analysis?.corrections||[]).map((_,i)=>i));
  const td=totalsDetail(rep,accepted);
  (td?.totalCorrections||[]).forEach(t=>accepted.add(t.correctionKey));
  return accepted;
}
