function renderDash(rep){
  const a=rep.analysis||{},k=computeKpis(rep);
  const degraded=["header_mapped_unverified","llm_mapped_unverified","user_mapped_unverified","unmapped"].includes(rep.overall_status);
  const kpis=a.schema==="cc"?[
    ["Total revenue",fmtM(k.total_revenue)],
    ["Total cost",fmtM(k.total_cost)],
    ["Gross profit",fmtM(k.total_gross_profit)],
    ["Realized margin",k.realized_margin==null?"—":(100*k.realized_margin).toFixed(1)+"%"],
  ]:[
    ["Contract value",fmtM(k.total_contract_value)],
    ["UEGP",fmtM(k.uegp)],
    ["Cost to complete",fmtM(k.cost_to_complete)],
    ["Earned revenue",fmtM(k.earned_revenue)],
    ["GP %",k.gp_pct==null?"—":(100*k.gp_pct).toFixed(1)+"%"],
    ["Net over/(under)",fmtM(k.net_billing_position)],
  ];
  const sigs=computeSignals(rep).map(s=>{
    const tier=s.severity>=.66?"high":s.severity>=.33?"med":"low";
    const shown=(s.jobs||[]).length;
    const body=s.cols?`<table class="sigtab"><thead><tr><th>Job</th>${s.cols.map(c=>`<th>${c}</th>`).join("")}</tr></thead><tbody>${
      (s.jobs||[]).map(j=>`<tr${j.row!=null?` data-row="${j.row}" title="View in schedule"`:""}><td>${j.label}</td>${(j.cells||[j.detail]).map(c=>`<td class="num">${c}</td>`).join("")}</tr>`).join("")
    }</tbody></table>${s.count>shown?`<p class="signal-more">Showing ${shown} of ${s.count}; every flagged job remains marked in the validated WIP and included in the trajectory filters.</p>`:""}`:`<div class="jobs">${(s.jobs||[]).map(j=>`<span class="chip">${j.label} · ${j.detail}</span>`).join("")}</div>`;
    return `<div class="item">
      <div class="head"><span class="sevr ${tier}">${tier}</span><p class="hl">${s.headline}</p></div>
      <details class="why" open><summary>Why it matters</summary><p>${s.why}</p></details>
      ${body}
    </div>`}).join("");
  const timeSignals=BATCH_ANALYSIS_MODE?timeSeriesSectionHTML(rep):"";
  const signalBody=(sigs||timeSignals)
    ?sigs+timeSignals
    :`<p class="empty">Nothing above threshold in this book.</p>`;
  const documentScope=rep._combinedSectionCount>1
    ?`<span class="analysis-scope-label">Entire WIP · ${rep._combinedPageLabel} · ${tableJobCount(rep.table)} jobs</span>`:"<span></span>";
  $("#dash").innerHTML=`
    <div class="analysis-topline">${BATCH_MODE&&BATCH_ACTIVE<0?analysisSwitcherHTML():documentScope}
      <div class="analysis-actions"><button class="btn primary" id="openJobAnalysis">Independent job analysis</button></div></div>
    <div class="report-title"><div><h2>Underwriting report</h2>${rep.source?`<p>${htmlEsc(rep.source)}</p>`:""}</div>
      <button class="btn" id="printAnalysis">Print report</button></div>
    ${degraded?`<div class="banner">${rep.overall_status==="user_mapped_unverified"
      ?"Column assignments were reviewed before calculation but could not be mathematically certified."
      :"Column names were read from headers and could not be mathematically verified. Figures below are the document's claims."}</div>`:""}
    <div class="kpis">${kpis.map(([l,v])=>`<div class="kpi"><div class="lab">${l}</div><div class="val num ${String(v).startsWith("(")?"neg":""}">${v}</div></div>`).join("")}</div>
    ${BATCH_ANALYSIS_MODE?timeSeriesKpiStripHTML(rep):""}
    <div class="cols">
      <div class="card"><h3>Underwriting signals <small>What stands out for underwriting? Click any job to open its analysis</small></h3>
        ${signalBody}</div>
    </div>
    ${billingTrajectoryChart(rep)}
    ${bookShape(rep)}
    <p class="foot">${footMeta(rep)}wipple does not store documents</p>`;
  $("#dash").querySelectorAll(".sigtab tbody tr[data-row]").forEach(tr=>{
    tr.onclick=()=>openWipModal(rep,+tr.dataset.row)});
  $("#dash").querySelectorAll(".time-series-section tbody tr[data-group]").forEach(tr=>{
    tr.onclick=()=>openGroupInValidatedWip(rep,tr.dataset.group)});
  $("#dash").querySelectorAll(".eub-job[data-group]").forEach(b=>{
    b.onclick=()=>openGroupInValidatedWip(rep,b.dataset.group)});
  $("#dash").querySelectorAll("#bookshape circle[data-row]").forEach(circle=>{
    const open=()=>openWipModal(rep,+circle.dataset.row);circle.onclick=open;
    circle.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open();}};
  });
  wireBillingTrajectory(rep);
  const portfolioSwitch=$("#dash .analysis-portfolio-switch");
  if(portfolioSwitch)portfolioSwitch.onclick=renderPortfolioAnalysis;
  const jobsOpen=$("#openJobAnalysis");
  if(jobsOpen)jobsOpen.onclick=()=>openWipModal(rep);
  $("#dash").querySelectorAll(".analysis-period-switch").forEach(button=>
    button.onclick=()=>renderPeriodAnalysis(button.dataset.period));
  $("#printAnalysis").onclick=()=>printSummary(rep);
}

function currentJobs(rep,accepted=ACCEPTED){
  const a=rep.analysis||{};
  const jobs=(a.jobs||[]).map(j=>({...j}));
  const corrs=a.corrections||[];
  const touched=new Set();
  corrs.forEach((c,ci)=>{
    if(!c.variable)return;
    const j=jobs[c.row];if(!j)return;
    j[c.variable]=accepted.has(ci)?c.implied:c.printed;
    if(accepted.has(ci)&&(c.variable==="E"||c.variable==="B"))touched.add(c.row);
  });
  jobs.forEach((j,r)=>{
    if(j.C>0)j.P=j.D/j.C;
    if(j.V>0)j.margin=(j.V-j.C)/j.V;
    if(j.E!=null&&j.B!=null){
      j.net=j.B-j.E;
      /* printed Under/Over columns are authoritative for the as-printed
         document; re-derive only where an applied correction changed E or B */
      if(touched.has(r)||j.U==null){j.U=Math.max(j.E-j.B,0);j.O=Math.max(j.B-j.E,0);}
    }
  });
  return jobs;
}
function computeKpis(rep){
  const jobs=currentJobs(rep);
  if(!jobs.length)return (rep.analysis||{}).kpis||{};
  const sum=k=>jobs.reduce((s,j)=>s+(j[k]??0),0);
  const tcv=sum("V"),estgp=sum("V")-sum("C");
  const hasE=jobs.every(j=>j.E!=null),hasB=jobs.every(j=>j.B!=null);
  return {total_contract_value:tcv,
    uegp:hasE?estgp-(sum("E")-sum("D")):null,
    cost_to_complete:sum("C")-sum("D"),
    earned_revenue:hasE?sum("E"):null,
    gp_pct:tcv?estgp/tcv:null,
    net_billing_position:hasE&&hasB?sum("B")-sum("E"):null};
}
/*SIGSTART -- mirror of analysis.py compute_signals; keep in lockstep.
  Server TUNE (analysis.tuning) overrides these defaults when present, so a
  constant retuned server-side propagates here without an edit. */
const SIG_T={ub_ratio_floor:.50,ub_ratio_full:1.00,ob_ratio_floor:.15,ob_ratio_full:.75,min_flag_dollars:10000,loss_margin_full:.05,overrun_full:.10,early_p:.15,early_share_floor:.25,early_share_full:.70,outlier_z_floor:2.0,outlier_z_full:5.0,thin_margin_p:.05,thin_share_floor:.20,thin_share_full:.60,big_job_floor:.20,big_job_full:.50,fade_gap_full:.08,fade_min_p:.35,min_signal_severity:.12};
const sMoney=x=>"$"+Math.round(x).toLocaleString("en-US");
const pct0=x=>Math.round(100*x)+"%";
const pct1=x=>(100*x).toFixed(1)+"%";
const clamp01=x=>Math.max(0,Math.min(1,x));
const median=a=>{const b=[...a].sort((x,y)=>x-y),m=b.length>>1;return b.length%2?b[m]:(b[m-1]+b[m])/2};
const pctile=(a,q)=>{const b=[...a].sort((x,y)=>x-y),i=(b.length-1)*q,lo=Math.floor(i),hi=Math.ceil(i);return b[lo]+(b[hi]-b[lo])*(i-lo)};
function computeSignals(rep){
  const jobs=currentJobs(rep);
  if(!jobs.length||!jobs.every(j=>j.C!=null&&j.D!=null&&j.V!=null))return (rep.analysis||{}).signals||[];
  const T={...SIG_T,...((rep.analysis||{}).tuning||{})},n=jobs.length;
  const V=jobs.map(j=>j.V),C=jobs.map(j=>j.C),D=jobs.map(j=>j.D);
  const hasEB=jobs.every(j=>j.E!=null&&j.B!=null);
  const E=hasEB?jobs.map(j=>j.E):null,B=hasEB?jobs.map(j=>j.B):null;
  const P=jobs.map(j=>j.C>0?j.D/j.C:0),m=jobs.map(j=>j.V>0?(j.V-j.C)/j.V:0);
  const U=hasEB?jobs.map((j,i)=>j.U!=null?j.U:Math.max(E[i]-B[i],0)):null;
  const O=hasEB?jobs.map((j,i)=>j.O!=null?j.O:Math.max(B[i]-E[i],0)):null;
  const lab=i=>jobs[i].label,out=[];
  if(U&&E){const rows=[];for(let i=0;i<n;i++){if(U[i]<T.min_flag_dollars)continue;if(P[i]>1.02)continue;const remRev=V[i]-E[i];
   /* A materially underbilled job with essentially no revenue left to earn has
      no runway to recover through billings — that is the worst case of this
      signal, not an exclusion. */
   const noRunway=P[i]>=.995||remRev<=Math.max(1,.005*Math.abs(V[i]));
   const ratio=noRunway?Infinity:U[i]/remRev;
   const sev=noRunway?1:clamp01((ratio-T.ub_ratio_floor)/(T.ub_ratio_full-T.ub_ratio_floor));if(sev>T.min_signal_severity)rows.push([sev,i,ratio]);}
   if(rows.length){rows.sort((a,b)=>b[0]-a[0]);const dollars=rows.reduce((s,r)=>s+U[r[1]],0),k=rows.length;
    const allJobs=rows.map(r=>({label:lab(r[1]),row:r[1],severity:r[0],dollars:Math.round(U[r[1]]),cells:[pct0(P[r[1]]),sMoney(U[r[1]]),Number.isFinite(r[2])&&r[2]<1?pct0(r[2]):"no runway"],detail:Number.isFinite(r[2])&&r[2]<1?`${pct0(P[r[1]])} complete, ${sMoney(U[r[1]])} underbilled = ${pct0(r[2])} of remaining revenue`:`${pct0(P[r[1]])} complete, ${sMoney(U[r[1]])} underbilled — more than all remaining revenue`}));
    out.push({id:"trapped_cash",count:k,severity:rows[0][0],dollars,
     headline:"Significant under billings with limited time to recover",
     cols:["Complete","Underbilled","Of remaining revenue"],allJobs,
     jobs:allJobs.slice(0,5),
     why:"Earned revenue the contractor has not billed. On nearly-finished work this usually means unapproved change orders or receivables that may not convert to cash."});}}
  if(hasEB){const rows=[];for(let i=0;i<n;i++){
    const ctc=Math.max(C[i]-D[i],0),remainingBillings=V[i]-B[i];
    const borrow=Math.max(ctc-remainingBillings,0);
    if(borrow<T.min_flag_dollars||ctc<=0)continue;
    const ratio=borrow/ctc;
    const sev=clamp01((ratio-T.ob_ratio_floor)/(T.ob_ratio_full-T.ob_ratio_floor));
    if(sev>T.min_signal_severity)rows.push([sev,i,borrow,ctc,remainingBillings,ratio]);
   }
   if(rows.length){rows.sort((a,b)=>b[0]-a[0]);const dollars=rows.reduce((s,r)=>s+r[2],0);
    const allJobs=rows.map(r=>({label:lab(r[1]),row:r[1],severity:r[0],dollars:Math.round(r[2]),
       cells:[pct0(P[r[1]]),sMoney(r[3]),sMoney(r[4]),sMoney(r[2])],
       detail:`${sMoney(r[3])} to finish, ${sMoney(r[4])} left to bill, ${sMoney(r[2])} pulled forward`}));
    out.push({id:"job_borrow",count:rows.length,severity:rows[0][0],dollars,
     headline:`${rows.length} job${rows.length===1?" shows":"s show"} ${sMoney(dollars)} of possible job borrow`,
     cols:["Complete","Cost to finish","Left to bill","Job borrow"],allJobs,
     jobs:allJobs.slice(0,5),
     why:"Billings exceed cost incurred plus the job's total projected profit. The excess is not free cash reserve; it has been pulled forward from the remaining work."});}}
  {const rows=[];for(let i=0;i<n;i++)if(m[i]<0){const sev=clamp01(-m[i]/T.loss_margin_full);if(sev>T.min_signal_severity)rows.push([sev,i]);}
   if(rows.length){rows.sort((a,b)=>b[0]-a[0]);const dollars=rows.reduce((s,r)=>s+(C[r[1]]-V[r[1]]),0);
    const allJobs=rows.map(r=>({label:lab(r[1]),row:r[1],severity:r[0],dollars:Math.round(C[r[1]]-V[r[1]]),cells:[pct1(m[r[1]]),sMoney(C[r[1]]-V[r[1]])],detail:`estimated margin ${pct1(m[r[1]])}`}));
    out.push({id:"loss_jobs",count:rows.length,severity:rows[0][0],dollars,
     headline:`${rows.length} job${rows.length>1?"s":""} estimated to lose ${sMoney(dollars)}`,
     cols:["Est. margin","Expected loss"],allJobs,
     jobs:allJobs.slice(0,5),
     why:"GAAP requires the full expected loss to be recognized immediately, not as the work progresses. A loss job on the schedule is a direct hit to the indemnitor's net worth."});}}
  {const rows=[];for(let i=0;i<n;i++)if(D[i]>C[i]){const sev=clamp01(((D[i]-C[i])/Math.max(C[i],1))/T.overrun_full);if(sev>T.min_signal_severity)rows.push([sev,i]);}
   if(rows.length){rows.sort((a,b)=>b[0]-a[0]);const dollars=rows.reduce((s,r)=>s+(D[r[1]]-C[r[1]]),0);
    out.push({id:"cost_overrun",count:rows.length,severity:rows[0][0],dollars,
     headline:`${rows.length} job${rows.length>1?"s":""} already ${sMoney(dollars)} past estimated cost`,
     cols:["Costs vs estimate","Over by"],
     jobs:rows.slice(0,5).map(r=>({label:lab(r[1]),row:r[1],dollars:Math.round(D[r[1]]-C[r[1]]),cells:[pct0(D[r[1]]/Math.max(C[r[1]],1)),sMoney(D[r[1]]-C[r[1]])],detail:`costs at ${pct0(D[r[1]]/Math.max(C[r[1]],1))} of estimate`})),
     why:"Cost to date exceeding the total estimate means the estimate is stale and the stated gross profit is fiction until re-estimated."});}}
  {const tcv=V.reduce((a,b)=>a+b,0),eIdx=[];for(let i=0;i<n;i++)if(P[i]<T.early_p)eIdx.push(i);
   if(tcv>0&&eIdx.length){const eV=eIdx.reduce((s,i)=>s+V[i],0),share=eV/tcv;
    const sev=clamp01((share-T.early_share_floor)/(T.early_share_full-T.early_share_floor));
    if(sev>T.min_signal_severity)out.push({id:"early_concentration",count:eIdx.length,severity:sev,dollars:Math.round(eV),
     headline:`${pct0(share)} of contract value is on jobs under ${pct0(T.early_p)} complete`,
     cols:["Complete","Contract value"],
     jobs:eIdx.slice(0,5).map(i=>({label:lab(i),row:i,dollars:Math.round(V[i]),cells:[pct0(P[i]),sMoney(V[i])],detail:`${pct0(P[i])} complete`})),
     why:"Early-stage estimates are unproven. A book concentrated at the front of the lifecycle has margins that exist mostly on paper."});}}
  if(n>=6){const med=median(m),iqr=(pctile(m,.75)-pctile(m,.25))||0.01,rows=[];
   for(let i=0;i<n;i++){const z=Math.abs(m[i]-med)/iqr;const sev=clamp01((z-T.outlier_z_floor)/(T.outlier_z_full-T.outlier_z_floor));
    if(sev>T.min_signal_severity&&m[i]>med)rows.push([sev,i]);}
   if(rows.length){rows.sort((a,b)=>b[0]-a[0]);
    out.push({id:"margin_outlier",count:rows.length,severity:rows[0][0],dollars:Math.round(rows.reduce((s,r)=>s+(V[r[1]]-C[r[1]]),0)),
     headline:`${rows.length} job${rows.length>1?"s":""} claiming margin far above this contractor's own norm`,
     cols:["Margin","Book median","Est. GP"],
     jobs:rows.slice(0,5).map(r=>({label:lab(r[1]),row:r[1],dollars:Math.round(V[r[1]]-C[r[1]]),cells:[pct1(m[r[1]]),pct1(med),sMoney(V[r[1]]-C[r[1]])],detail:`${pct1(m[r[1]])} margin vs book median ${pct1(med)}`})),
     why:"Judged against this contractor's own bidding history on this schedule, not an industry benchmark. An outlier margin at mid-completion is the classic shape of profit fade that has not been recognized yet."});}}
  {const ctcAll=jobs.map((j,i)=>Math.max(C[i]-D[i],0));const totalCtc=ctcAll.reduce((a,b)=>a+b,0);
   const thin=[];for(let i=0;i<n;i++)if(m[i]>=0&&m[i]<T.thin_margin_p&&ctcAll[i]>0)thin.push(i);
   if(totalCtc>0&&thin.length){const thinCtc=thin.reduce((s,i)=>s+ctcAll[i],0),share=thinCtc/totalCtc;
    const sev=clamp01((share-T.thin_share_floor)/(T.thin_share_full-T.thin_share_floor));
    if(sev>T.min_signal_severity){thin.sort((a,b)=>ctcAll[b]-ctcAll[a]);
     out.push({id:"thin_margin_backlog",count:thin.length,severity:sev,dollars:Math.round(thinCtc),
      headline:`${pct0(share)} of remaining cost sits on jobs with margin under ${pct0(T.thin_margin_p)}`,
      cols:["Margin","Still to build"],
      jobs:thin.slice(0,5).map(i=>({label:lab(i),row:i,dollars:Math.round(ctcAll[i]),cells:[pct1(m[i]),sMoney(ctcAll[i])],detail:`${pct1(m[i])} margin, ${sMoney(ctcAll[i])} still to build`})),
      why:"Thin-margin work has no cushion: a small overrun flips it to a loss. When much of the remaining book is fragile, one bad quarter can erase the schedule's stated profit."});}}}
  {const tcv=V.reduce((a,b)=>a+b,0);
   if(tcv>0&&n>=2){let top=0;for(let i=1;i<n;i++)if(V[i]>V[top])top=i;const share=V[top]/tcv;
    const sev=clamp01((share-T.big_job_floor)/(T.big_job_full-T.big_job_floor));
    if(sev>T.min_signal_severity)out.push({id:"job_concentration",count:1,severity:sev,dollars:Math.round(V[top]),
     headline:`Largest job is ${pct0(share)} of the program`,
     cols:["Share","Complete","Margin"],
     jobs:[{label:lab(top),row:top,dollars:Math.round(V[top]),cells:[pct0(share),pct0(P[top]),pct1(m[top])],detail:`${pct0(P[top])} complete, ${pct1(m[top])} margin`}],
     why:"The program's outcome is coupled to one job. Whatever happens on it -- fade, dispute, slow pay -- happens to the contractor."});}}
  /* fade proxy: only when E carries information beyond V*D/C. The guard is
     numeric rather than provenance-based on purpose -- it stays correct
     even after a reviewer edits E through a correction. */
  if(E){const eDerived=jobs.every((j,i)=>Math.abs(E[i]-(C[i]>0?V[i]*D[i]/C[i]:0))<=Math.max(1,.005*V[i]));
   if(!eDerived){const rows=[];
    for(let i=0;i<n;i++){if(P[i]<T.fade_min_p||E[i]<=0)continue;
     const earnedM=(E[i]-D[i])/E[i],gap=m[i]-earnedM;
     const sev=clamp01(P[i])*clamp01(gap/T.fade_gap_full);
     if(sev>T.min_signal_severity&&gap*E[i]>=T.min_flag_dollars)rows.push([sev,i,earnedM,gap]);}
    if(rows.length){rows.sort((a,b)=>b[0]-a[0]);const dollars=rows.reduce((s,r)=>s+r[3]*E[r[1]],0);
     out.push({id:"unrecognized_fade",count:rows.length,severity:rows[0][0],dollars:Math.round(dollars),
      headline:`${rows.length} job${rows.length>1?"s":""} earning below the stated final margin`,
      cols:["Complete","Earned to date","Est. final"],
      jobs:rows.slice(0,5).map(r=>({label:lab(r[1]),row:r[1],dollars:Math.round(r[3]*E[r[1]]),cells:[pct0(P[r[1]]),pct1(r[2]),pct1(m[r[1]])],detail:`${pct0(P[r[1]])} complete, earned ${pct1(r[2])} to date vs ${pct1(m[r[1]])} estimated final`})),
      why:"For the stated margin to hold, the remaining work must out-earn everything built so far. Margin estimates that survive on the back half of a job are rare; this is where fade hides before it is recognized."});}}}
  out.forEach(x=>x.severity=Math.round(x.severity*1000)/1000);
  out.sort((a,b)=>(b.severity-a.severity)||(b.dollars-a.dollars));
  return out;
}
/*SIGEND*/
const TRAJECTORY_FLAGS=[
  {id:"trapped_cash",label:"Late under billings"},
  {id:"job_borrow",label:"Job borrow"},
  {id:"loss_jobs",label:"Jobs losing money"},
  {id:"profit_fade",label:"Profit fade"}
];
const TRAJECTORY_COLORS=[
  "#3F6FB6","#A3402F","#2E7A72","#7656A5","#9A6A16","#B05278",
  "#4D7C3A","#8D583A","#4A6D88","#9B4F44","#5D6F30","#6E5A8E"
];
function reportRowForGroup(rep,groupId){
  return (rep?._consolidatedRows||[]).findIndex(r=>r.group?.id===groupId);
}
function openGroupInValidatedWip(rep,groupId){
  let target=rep,row=reportRowForGroup(target,groupId);
  if(row<0&&MATCH_STATE){
    const group=MATCH_STATE.groups.find(g=>g.id===groupId);
    const periods=[...new Set((group?.observations||[]).map(o=>o.period))].sort().reverse();
    for(const period of periods){
      const candidate=periodAnalysisReport(period),candidateRow=reportRowForGroup(candidate,groupId);
      if(candidateRow>=0){target=candidate;row=candidateRow;break;}
    }
  }
  if(row>=0)openWipModal(target,row);
}
function trajectoryFlagData(rep){
  const membership=new Map(TRAJECTORY_FLAGS.map(f=>[f.id,new Set()]));
  const scores=new Map(TRAJECTORY_FLAGS.map(f=>[f.id,new Map()]));
  for(const signal of computeSignals(rep)){
    if(!membership.has(signal.id))continue;
    for(const job of (signal.allJobs||signal.jobs||[])){
      const group=rep?._consolidatedRows?.[job.row]?.group;
      if(!group)continue;
      membership.get(signal.id).add(group.id);
      const score=Number.isFinite(job.severity)?job.severity:(signal.severity||0);
      scores.get(signal.id).set(group.id,Math.max(scores.get(signal.id).get(group.id)||0,score));
    }
  }
  const trend=timeSeriesData();
  for(const row of (trend?.rows||[]))if(row.materialFade&&row.groupId){
    membership.get("profit_fade").add(row.groupId);
    const marginScore=Math.abs(row.marginChange||0)/.10;
    const dollarScore=Math.abs(row.fade||0)/Math.max(.05*Math.abs(row.scale||1),1);
    scores.get("profit_fade").set(row.groupId,clamp01(Math.max(marginScore,dollarScore)));
  }
  return{membership,scores};
}
function billingTrajectoryChart(rep){
  if(!BATCH_ANALYSIS_MODE||!MATCH_STATE)return"";
  const{membership,scores}=trajectoryFlagData(rep);
  const available=TRAJECTORY_FLAGS.filter(f=>membership.get(f.id).size);
  if(!available.length)return"";
  const active=new Set([...BILLING_TRAJECTORY_FILTERS].filter(id=>membership.get(id)?.size));
  const selectedIds=new Set();
  for(const id of active)for(const groupId of membership.get(id)||[])selectedIds.add(groupId);
  const XMIN=.15,YMAX=.20,TOP_N=5,W=960,H=330,L=58,R=18,T=24,B=42;
  const x=p=>L+(W-L-R)*(Math.max(XMIN,Math.min(1,p))-XMIN)/(1-XMIN);
  const y=v=>T+(H-T-B)*(1-(Math.max(-YMAX,Math.min(YMAX,v))+YMAX)/(2*YMAX));
  const trajectories=[];
  for(const groupId of selectedIds){
    const group=MATCH_STATE.groups.find(g=>g.id===groupId);if(!group)continue;
    const points=groupPeriodSnapshots(group).map(snapshot=>{
      const v=snapshot.vars||{},p=v.P??(v.C?v.D/v.C:null),contract=v.V;
      const net=v.N??(v.B!=null&&v.E!=null?v.B-v.E:null);
      return{p,position:contract&&net!=null?net/contract:null,period:snapshot.period,
        name:snapshot.name||snapshot.id||"Job",id:snapshot.id||""};
    }).filter(point=>Number.isFinite(point.p)&&Number.isFinite(point.position)&&point.p>=XMIN&&point.p<=1.000001)
      .sort((a,b)=>a.period.localeCompare(b.period));
    if(!points.length)continue;
    const flagIds=TRAJECTORY_FLAGS.map(f=>f.id).filter(id=>active.has(id)&&membership.get(id)?.has(groupId));
    const score=Math.max(0,...flagIds.map(id=>scores.get(id)?.get(groupId)||0));
    const latest=points[points.length-1];
    trajectories.push({groupId,points,flagIds,score,label:latest.name||latest.id||"Job",
      latestCompletion:latest.p,latestPosition:latest.position});
  }
  trajectories.sort((a,b)=>(b.score-a.score)||(b.latestCompletion-a.latestCompletion)
    ||(Math.abs(b.latestPosition)-Math.abs(a.latestPosition))||a.label.localeCompare(b.label));
  const total=trajectories.length;
  const displayed=BILLING_TRAJECTORY_SHOW_ALL?trajectories:trajectories.slice(0,TOP_N);
  displayed.forEach((trajectory,index)=>trajectory.color=TRAJECTORY_COLORS[index%TRAJECTORY_COLORS.length]);

  const corridor=HEALTHY_BILLING_CORRIDOR;
  const corridorPath=[...corridor.map(d=>`${x(d[0]).toFixed(1)},${y(d[1]).toFixed(1)}`),
    ...[...corridor].reverse().map(d=>`${x(d[0]).toFixed(1)},${y(d[2]).toFixed(1)}`)].join(" ");
  const yTicks=[-.20,-.10,0,.10,.20].map(v=>`<g>${v===0?"":`<line x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}" stroke="var(--line-soft)"/>`}<text x="${L-8}" y="${y(v)+3}" text-anchor="end" font-size="10" fill="var(--muted)">${v>0?"+":""}${Math.round(v*100)}%</text></g>`).join("");
  const xTicks=[.15,.25,.50,.75,1].map(v=>`<g><line x1="${x(v)}" y1="${H-B}" x2="${x(v)}" y2="${H-B+4}" stroke="var(--line)"/><text x="${x(v)}" y="${H-15}" text-anchor="middle" font-size="10" fill="var(--muted)">${Math.round(v*100)}%</text></g>`).join("");
  const drawn=displayed.filter(t=>!BILLING_TRAJECTORY_HIDDEN.has(t.groupId));
  const lines=drawn.map(({groupId,points,color,label})=>{
    const coords=points.map(p=>`${x(p.p).toFixed(1)},${y(p.position).toFixed(1)}`).join(" ");
    const latest=points[points.length-1];
    const title=`${label}: ${points.length} statement${points.length===1?"":"s"}; latest ${Math.round(latest.p*100)}% complete, ${(latest.position*100).toFixed(1)}% billing position`;
    const segment=points.length>1?`<polyline class="trajectory-hit" points="${coords}"><title>${htmlEsc(title)}</title></polyline><polyline class="trajectory-line" points="${coords}" stroke="${color}"/>`:"";
    const dots=points.map(point=>{
      const clipped=Math.abs(point.position)>YMAX,cy=y(point.position),symbol=point.position>YMAX?"↑":point.position<-YMAX?"↓":"";
      return`<circle class="trajectory-point" cx="${x(point.p).toFixed(1)}" cy="${cy.toFixed(1)}" r="4.7" fill="${color}"><title>${htmlEsc(label)} · ${displayPeriod(point.period)} · ${(point.p*100).toFixed(0)}% complete · ${(point.position*100).toFixed(1)}% of contract${clipped?" (shown at chart edge)":""}</title></circle>${clipped?`<text class="trajectory-clip" x="${x(point.p).toFixed(1)}" y="${(cy+(point.position>0?14:-9)).toFixed(1)}" text-anchor="middle" fill="${color}">${symbol}</text>`:""}`;
    }).join("");
    return`<g class="trajectory-series" data-group="${htmlEsc(groupId)}">${segment}${dots}</g>`;
  }).join("");
  const controls=TRAJECTORY_FLAGS.map(f=>{
    const count=membership.get(f.id).size,on=active.has(f.id);
    return`<button class="trajectory-toggle${on?" on":""}" data-filter="${f.id}" ${count?"":"disabled"} aria-pressed="${on}">${f.label}<span class="count">${count}</span></button>`;
  }).join("");
  const legend=displayed.length?`<div class="trajectory-legend" aria-label="Displayed jobs — click to show or hide, arrow to open">${displayed.map(t=>{
    const off=BILLING_TRAJECTORY_HIDDEN.has(t.groupId);
    return`<button class="trajectory-legend-item${off?" off":""}" data-legend="${htmlEsc(t.groupId)}" aria-pressed="${!off}" title="${off?"Show":"Hide"} ${htmlEsc(t.label)} on the chart"><span class="trajectory-legend-dot" style="background:${t.color}"></span><span class="trajectory-legend-label">${htmlEsc(t.label)}</span><small>${t.points.length} stmt${t.points.length===1?"":"s"}</small><span class="trajectory-legend-open" role="button" tabindex="0" data-open="${htmlEsc(t.groupId)}" title="Open ${htmlEsc(t.label)}" aria-label="Open ${htmlEsc(t.label)}">\u2197</span></button>`;
  }).join("")}</div>`:"";
  const hiddenCount=displayed.filter(t=>BILLING_TRAJECTORY_HIDDEN.has(t.groupId)).length;
  const showToggle=total>TOP_N?`<button class="trajectory-show-toggle" id="trajectoryShowAll">${BILLING_TRAJECTORY_SHOW_ALL?`Show highest-priority ${TOP_N}`:`Show all ${total}`}</button>`:"";
  const summary=(active.size===0?"No signals selected":total===0?"No linked histories match the selected signals":BILLING_TRAJECTORY_SHOW_ALL
    ?`Showing all ${total} matching jobs`:`Showing ${displayed.length} of ${total} matching jobs, prioritized by signal severity`)
    +(hiddenCount?` · ${hiddenCount} hidden from the key`:"");
  const empty=active.size===0?"Select one or more signals to display job trajectories.":"No linked job histories match the selected signals.";
  return`<div class="card trajectory-card" id="billingTrajectory"><div class="trajectory-head"><h3>Flagged job billing trajectories
      <small>Statement-to-statement billing position; click a line, dot, or job label to open the validated WIP row</small></h3><div class="trajectory-controls">${controls}</div></div>
    <div class="trajectory-meta"><span class="trajectory-summary">${summary}</span>${showToggle}</div>${legend}
    ${displayed.length?`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;margin-top:8px" role="img" aria-label="Billing position trajectories for flagged jobs"><polygon points="${corridorPath}" fill="#43653A" opacity=".17"/>${yTicks}<line x1="${L}" y1="${y(0)}" x2="${W-R}" y2="${y(0)}" stroke="var(--ink)" stroke-opacity=".58" stroke-width="2.5"/>${xTicks}${lines}<text x="${(L+W-R)/2}" y="${H-2}" text-anchor="middle" font-size="10" fill="var(--muted)">% complete (shown from 15% — early positions are unstable)</text><text transform="translate(13 ${(T+H-B)/2}) rotate(-90)" text-anchor="middle" font-size="10" fill="var(--muted)">billing position (% of contract)</text></svg>`:`<div class="trajectory-empty">${empty}</div>`}
    <p class="trajectory-note">Shading shows a healthy-job range, not a cutoff. Values beyond ±20% are shown at the chart edge.</p></div>`;
}
function wireBillingTrajectory(rep){
  const root=$("#billingTrajectory");if(!root)return;
  root.querySelectorAll(".trajectory-toggle").forEach(button=>button.onclick=()=>{
    const id=button.dataset.filter;
    if(BILLING_TRAJECTORY_FILTERS.has(id))BILLING_TRAJECTORY_FILTERS.delete(id);
    else BILLING_TRAJECTORY_FILTERS.add(id);
    BILLING_TRAJECTORY_SHOW_ALL=false;
    root.outerHTML=billingTrajectoryChart(rep);wireBillingTrajectory(rep);
  });
  const showAll=root.querySelector("#trajectoryShowAll");
  if(showAll)showAll.onclick=()=>{
    BILLING_TRAJECTORY_SHOW_ALL=!BILLING_TRAJECTORY_SHOW_ALL;
    root.outerHTML=billingTrajectoryChart(rep);wireBillingTrajectory(rep);
  };
  const focus=groupId=>{
    root.querySelectorAll(".trajectory-series").forEach(series=>{
      series.classList.toggle("is-focused",series.dataset.group===groupId);
      series.classList.toggle("is-dimmed",Boolean(groupId)&&series.dataset.group!==groupId);
    });
  };
  root.querySelectorAll(".trajectory-legend-item").forEach(item=>{
    item.onclick=e=>{
      if(e.target.closest("[data-open]"))return;
      const id=item.dataset.legend;
      if(BILLING_TRAJECTORY_HIDDEN.has(id))BILLING_TRAJECTORY_HIDDEN.delete(id);
      else BILLING_TRAJECTORY_HIDDEN.add(id);
      root.outerHTML=billingTrajectoryChart(rep);wireBillingTrajectory(rep);
    };
    item.onmouseenter=()=>focus(item.dataset.legend);item.onmouseleave=()=>focus(null);
    item.onfocus=()=>focus(item.dataset.legend);item.onblur=()=>focus(null);
  });
  root.querySelectorAll("[data-open]").forEach(node=>{
    const open=e=>{e.stopPropagation();openGroupInValidatedWip(rep,node.dataset.open);};
    node.onclick=open;
    node.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();open(e);}};
  });
  root.querySelectorAll(".trajectory-series[data-group]").forEach(node=>{
    node.onclick=e=>{e.stopPropagation();openGroupInValidatedWip(rep,node.dataset.group);};
    node.onmouseenter=()=>focus(node.dataset.group);node.onmouseleave=()=>focus(null);
    node.onfocus=()=>focus(node.dataset.group);node.onblur=()=>focus(null);
  });
}
function bookShape(rep){
  const XMIN=.15,ymax=.08;
  const jobs=currentJobs(rep).map((j,row)=>({...j,_row:row}))
    .filter(j=>j.net!=null&&j.V>0&&j.P>=XMIN)
    .map(j=>j.P>=1-1e-6?{...j,net:0,U:0,O:0}:j);
  if(jobs.length<4)return"";
  const T={...SIG_T,...((rep.analysis||{}).tuning||{})};
  const W=960,H=210,L=46,R=14,Tm=18,Bm=34;
  const x=p=>L+(W-L-R)*(Math.min(Math.max(p,XMIN),1)-XMIN)/(1-XMIN), y=v=>{v=Math.max(-ymax,Math.min(ymax,v));return Tm+(H-Tm-Bm)*(1-(v+ymax)/(2*ymax))};
  /* dot color comes from the SAME severity math as the flag list, so the
     chart and the signals can never disagree about what is a problem */
  const sevOf=j=>{
    const U=Math.max(-(j.net),0),O=Math.max(j.net,0);
    if(U>=T.min_flag_dollars){
      const remRev=j.V-(j.E??j.V*j.P);
      if(remRev<=Math.max(1,.005*Math.abs(j.V)))return{s:1,side:"under"};
      const r=U/remRev;
      const s=clamp01((r-T.ub_ratio_floor)/(T.ub_ratio_full-T.ub_ratio_floor));
      if(s>T.min_signal_severity)return{s,side:"under"};
    }
    if(O>=T.min_flag_dollars){
      const ctc=(j.C??0)-(j.D??0);
      const r=ctc>0?O/ctc:Infinity;
      const s=clamp01((r-T.ob_ratio_floor)/(T.ob_ratio_full-T.ob_ratio_floor));
      if(s>T.min_signal_severity)return{s,side:"over"};
    }
    return null;
  };
  const dots=jobs.map(j=>{
    const f=j.net/j.V, r=4+9*Math.sqrt(j.V/Math.max(...jobs.map(q=>q.V)));
    const sev=sevOf(j);
    const col=sev?(sev.side==="under"?"var(--brick)":"var(--amber)"):"var(--sage)";
    const op=sev?(.45+.35*sev.s).toFixed(2):".45";
    const position=j.net===0?"balanced":`${j.net<0?"under":"over"}billed $${fmt$(Math.abs(j.net))} (${(100*f).toFixed(1)}% of contract)`;
    const clipped=Math.abs(f)>ymax;
    return `<circle data-row="${j._row}" tabindex="0" role="button" cx="${x(j.P).toFixed(1)}" cy="${y(f).toFixed(1)}" r="${r.toFixed(1)}" fill="${col}" fill-opacity="${op}" stroke="${col}" stroke-width="${clipped?2:1}"><title>${j.label}: ${(100*j.P).toFixed(0)}% complete, ${position}${sev?` — flagged, severity ${(100*sev.s).toFixed(0)}%`:""}${clipped?" — pinned to chart edge":""}. Click to view in the validated WIP.</title></circle>`;
  }).join("");
  /* the underbilling flag boundary U/(V-E)=floor is the straight line
     net/V = -floor*(1-P) in these axes -- exact when E is cost-to-cost */
  const cc=jobs.every(j=>j.E==null||Math.abs(j.E-j.V*j.P)<=Math.max(1,.005*j.V));
  let guide="";
  if(cc){
    const r0=T.ub_ratio_floor;
    const pStart=Math.max(0,1-ymax/r0);
    guide=`<line x1="${x(pStart).toFixed(1)}" y1="${y(-r0*(1-pStart)).toFixed(1)}" x2="${x(1).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="var(--brick)" stroke-width="1" stroke-dasharray="4 4" opacity=".5"/>
    <text x="${x(1)-4}" y="${y(-r0*(1-.97))+13}" text-anchor="end" font-size="9" fill="var(--brick)" opacity=".75">underbilling flag boundary</text>`;
  }
  const ticks=[.15,.25,.5,.75,1].map(p=>`<text x="${x(p)}" y="${H-12}" text-anchor="middle" font-size="10" fill="var(--muted)">${100*p}%</text>`).join("");
  return `<div class="card" id="bookshape" style="margin-bottom:18px">
    <h3>Billing position by completion <small>Each dot is a job, sized by contract value</small></h3>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;margin-top:8px" role="img" aria-label="Scatter of billing position against completion for each job">
      <line x1="${L}" y1="${y(0)}" x2="${W-R}" y2="${y(0)}" stroke="var(--line)" stroke-width="1"/>
      <text x="${L}" y="${Tm+2}" font-size="10" fill="var(--muted)">overbilled</text>
      <text x="${L}" y="${H-Bm-4}" font-size="10" fill="var(--muted)">underbilled</text>
      ${guide}${ticks}${dots}
    </svg>
    <p style="font-size:12px;color:var(--muted);margin:6px 0 0">Shown from 15% complete; earlier positions are omitted because small denominators make them unstable. Values outside ±8% are pinned to the chart edge. Click any bubble to view that job in the validated WIP. Colored dots use the same flag rules as the signals above: red for material late underbilling${cc?" (the dashed boundary)":""}, gold when overbilling is large against the cost left to finish.</p>
  </div>`;
}

function metricModelName(c){
  return c.response_model||c.configured_model||c.model||"unknown model";
}
function modelCostBreakdown(m){
  const calls=m.by_call||[];
  if(!calls.length)return"";
  const groups=new Map();
  for(const c of calls){
    const name=metricModelName(c);
    const key=`${c.provider||""}|${name}`;
    const g=groups.get(key)||{name,provider:c.provider||"",calls:0,cost:0,seconds:0};
    g.calls+=1;
    g.cost+=Number(c.cost_usd)||0;
    g.seconds+=Number(c.seconds)||0;
    groups.set(key,g);
  }
  return[...groups.values()].map(g=>{
    const count=g.calls>1?` ×${g.calls}`:"";
    const provider=g.provider?`${g.provider}: `:"";
    return`${provider}${g.name}${count} $${g.cost.toFixed(4)}`;
  }).join(" + ");
}
function footMeta(rep){
  const m=rep.metrics||{};const bits=[];
  if(m.elapsed_seconds!=null)bits.push(`${m.elapsed_seconds}s`);
  if(m.api_calls){
    const breakdown=modelCostBreakdown(m);
    bits.push(`${m.api_calls} model call${m.api_calls>1?"s":""}${breakdown?` · ${breakdown}`:""}`);
    const calls=m.by_call||[];
    const mismatch=calls.some(c=>c.requested_model&&
      c.requested_model!==(c.configured_model||c.model));
    if(mismatch){
      const requested=[...new Set(calls.map(c=>c.requested_model).filter(Boolean))].join(", ");
      bits.push(`requested ${requested}`);
    }
    bits.push(`total $${(+m.cost_usd).toFixed(4)}`);
  }
  return bits.length?bits.join(" · ")+" · ":"";
}
/* which schedule columns each signal points at, and its ink color.
   brick = money at risk, amber = leverage / estimate quality */
const SIG_MARK={
  trapped_cash:{color:"brick",vars:["U"],name:"Trapped cash"},
  job_borrow:{color:"amber",vars:["Q","RB","B"],name:"Job borrow"},
  loss_jobs:{color:"brick",vars:["G","M"],name:"Loss job"},
  cost_overrun:{color:"amber",vars:["D"],name:"Cost overrun"},
  early_concentration:{color:"amber",vars:["P"],name:"Early stage"},
  margin_outlier:{color:"amber",vars:["M","G"],name:"Margin outlier"},
  thin_margin_backlog:{color:"amber",vars:["M","G"],name:"Thin margin"},
  job_concentration:{color:"amber",vars:["V"],name:"Concentration"},
  unrecognized_fade:{color:"brick",vars:["E"],name:"Earning below estimate"}};
function signalRowFlags(rep){
  const map={};
  for(const s of computeSignals(rep)){
    const mk=SIG_MARK[s.id];if(!mk)continue;
    for(const j of (s.allJobs||s.jobs||[])){
      if(j.row==null)continue;
      (map[j.row]=map[j.row]||[]).push({id:s.id,...mk});
    }
  }
  return map;
}
/* --- job analysis modal ------------------------------------------------
   Master-detail replacement for the flat schedule modal: every job in a
   searchable left rail, one interpreted job on the right. The validated
   WIP *pages* (single-doc nav page and the batch consolidated page with
   its full-schedule modal) are untouched; this only changes what opens
   when a signal row, bubble, or trajectory is clicked. */
function jmSnapshotPoint(vars,period,type){
  const v=vars||{};
  const P=v.P??(v.C>0?v.D/v.C:null);
  const net=v.N??(v.B!=null&&v.E!=null?v.B-v.E:null);
  const V=v.V??null,C=v.C??null;
  const gp=v.G??(V!=null&&C!=null?V-C:null);
  return{period:period||"",type:type||"wip",P,net,V,C,gp,
    D:v.D??null,B:v.B??null,
    position:V>0&&net!=null?net/V:null,
    margin:V>0&&gp!=null?gp/V:null};
}
function jmHistory(rep,row){
  const group=rep._consolidatedRows?.[row]?.group||null;
  if(group){
    const pts=groupPeriodSnapshots(group).map(s=>jmSnapshotPoint(s.vars,s.period,s.type));
    if(pts.length)return{group,points:pts};
  }
  const j=currentJobs(rep)[row]||{};
  return{group,points:[jmSnapshotPoint(j,rep._period||"",j.P>=1-1e-6?"cc":"wip")]};
}
function jmIsCompletedGroup(g){
  const snaps=groupPeriodSnapshots(g);if(!snaps.length)return false;
  const last=snaps[snaps.length-1];
  if(last.type==="cc")return true;
  const v=last.vars||{},P=v.P??(v.C>0?v.D/v.C:null);
  return P!=null&&P>=.995;
}
function jmSimilarCompleted(rep,group,subjectV){
  if(!MATCH_STATE)return[];
  const done=MATCH_STATE.groups.filter(g=>g!==group&&jmIsCompletedGroup(g))
    .map(g=>{
      const points=groupPeriodSnapshots(g).map(s=>jmSnapshotPoint(s.vars,s.period,s.type));
      const last=points[points.length-1];
      return{label:last&&(groupPeriodSnapshots(g).slice(-1)[0].name||groupPeriodSnapshots(g).slice(-1)[0].id)||"Completed job",
        points,V:last?.V??null,finalMargin:last?.margin??null};
    }).filter(x=>x.points.length&&x.V>0);
  if(!done.length)return[];
  const sized=subjectV>0?done.filter(x=>x.V>=.35*subjectV&&x.V<=3*subjectV):done;
  const pool=sized.length?sized:done;
  return pool.sort((a,b)=>subjectV>0
    ?Math.abs(Math.log(a.V/subjectV))-Math.abs(Math.log(b.V/subjectV))
    :b.V-a.V).slice(0,3);
}
function jmShortPeriod(iso){
  if(!iso)return"Current";
  const[y,m,d]=iso.split("-").map(Number);
  return Number.isFinite(y)&&Number.isFinite(m)&&Number.isFinite(d)?`${m}/${d}/${y}`:iso;
}
function jmBillingChartSVG(points,overlays,showOverlay){
  const XMIN=.15;
  const usable=points.filter(p=>Number.isFinite(p.P)&&Number.isFinite(p.position)&&p.P>=XMIN&&p.P<=1.000001);
  if(!usable.length)return`<div class="jm-chart-empty">No billing position to plot yet — observations sit below 15% complete, where small denominators make positions unstable.</div>`;
  const overlayPts=showOverlay?overlays.map(o=>({label:o.label,
    pts:o.points.filter(p=>Number.isFinite(p.P)&&Number.isFinite(p.position)&&p.P>=XMIN&&p.P<=1.000001)}))
    .filter(o=>o.pts.length>1):[];
  const magnitudes=usable.map(p=>Math.abs(p.position))
    .concat(overlayPts.flatMap(o=>o.pts.map(p=>Math.abs(p.position))));
  const ymax=Math.max(...magnitudes)>.078?.20:.08;
  const W=460,H=210,L=46,R=12,T=14,B=32;
  const x=p=>L+(W-L-R)*(Math.min(Math.max(p,XMIN),1)-XMIN)/(1-XMIN);
  const y=v=>T+(H-T-B)*(1-(Math.max(-ymax,Math.min(ymax,v))+ymax)/(2*ymax));
  const corridor=HEALTHY_BILLING_CORRIDOR;
  const corridorPath=[...corridor.map(d=>`${x(d[0]).toFixed(1)},${y(d[1]).toFixed(1)}`),
    ...[...corridor].reverse().map(d=>`${x(d[0]).toFixed(1)},${y(d[2]).toFixed(1)}`)].join(" ");
  const yVals=ymax===.08?[-.08,-.04,0,.04,.08]:[-.20,-.10,0,.10,.20];
  const yTicks=yVals.map(v=>`<g>${v===0?"":`<line x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}" stroke="var(--line-soft)"/>`}<text x="${L-7}" y="${y(v)+3}" text-anchor="end" font-size="9.5" fill="var(--muted)">${v>0?"+":""}${Math.round(v*100)}%</text></g>`).join("");
  const xTicks=[.15,.25,.5,.75,1].map(v=>`<g><line x1="${x(v)}" y1="${H-B}" x2="${x(v)}" y2="${H-B+4}" stroke="var(--line)"/><text x="${x(v)}" y="${H-16}" text-anchor="middle" font-size="9.5" fill="var(--muted)">${Math.round(v*100)}%</text></g>`).join("");
  const overlayLines=overlayPts.map(o=>`<polyline points="${o.pts.map(p=>`${x(p.P).toFixed(1)},${y(p.position).toFixed(1)}`).join(" ")}" fill="none" stroke="#B4B2A9" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round"><title>${htmlEsc(o.label)} (completed)</title></polyline>`).join("");
  const coords=usable.map(p=>`${x(p.P).toFixed(1)},${y(p.position).toFixed(1)}`).join(" ");
  const line=usable.length>1?`<polyline points="${coords}" fill="none" stroke="#3F6FB6" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`:"";
  const dots=usable.map(p=>{
    const clipped=Math.abs(p.position)>ymax;
    return`<circle cx="${x(p.P).toFixed(1)}" cy="${y(p.position).toFixed(1)}" r="4.2" fill="#3F6FB6" stroke="var(--surface)" stroke-width="1.5"><title>${jmShortPeriod(p.period)} · ${(p.P*100).toFixed(0)}% complete · ${(p.position*100).toFixed(1)}% of contract${clipped?" (shown at chart edge)":""}</title></circle>`;
  }).join("");
  return`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Billing position by percent complete against a healthy-job range">
    <polygon points="${corridorPath}" fill="#43653A" opacity=".17"/>${yTicks}
    <line x1="${L}" y1="${y(0)}" x2="${W-R}" y2="${y(0)}" stroke="var(--ink)" stroke-opacity=".5" stroke-width="1.8"/>${xTicks}
    ${overlayLines}${line}${dots}
    <text x="${(L+W-R)/2}" y="${H-3}" text-anchor="middle" font-size="9.5" fill="var(--muted)">% complete (shown from 15% — early positions are unstable)</text>
  </svg>`;
}
function jmMarginChartSVG(points,overlays,showOverlay){
  const usable=points.filter(p=>Number.isFinite(p.margin));
  if(!usable.length)return`<div class="jm-chart-empty">No margin estimate available for this job.</div>`;
  /* Margins beyond ±100% are data artifacts, not information; they must not
     own the axis. Clamp the domain and pin outliers to the chart edge. */
  const CLAMP=1;
  const clamp=v=>Math.max(-CLAMP,Math.min(CLAMP,v));
  const finals=showOverlay?overlays.map(o=>o.finalMargin).filter(Number.isFinite).map(clamp):[];
  const values=usable.map(p=>clamp(p.margin)).concat(finals,[0]);
  let lo=Math.min(...values),hi=Math.max(...values);
  const pad=Math.max(.015,(hi-lo)*.18);
  lo=Math.max(-CLAMP,lo-pad);hi=Math.min(CLAMP,hi+pad);
  if(hi-lo<.02){hi+=.01;lo-=.01;}
  const W=460,H=210,L=46,R=12,T=14,B=32;
  const n=usable.length;
  const x=i=>n===1?(L+W-R)/2:L+(W-L-R)*(i/(n-1));
  const y=v=>T+(H-T-B)*(1-(clamp(v)-lo)/(hi-lo));
  const NICE=[.005,.01,.02,.025,.05,.1,.2,.25,.5];
  const step=NICE.find(s=>(hi-lo)/s<=6.5)||1;
  const ticks=[];for(let v=Math.ceil(lo/step-1e-9)*step;v<=hi+1e-9;v+=step)ticks.push(Math.round(v*10000)/10000);
  const yTicks=ticks.map(v=>`<g>${Math.abs(v)<1e-9?"":`<line x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}" stroke="var(--line-soft)"/>`}<text x="${L-7}" y="${y(v)+3}" text-anchor="end" font-size="9.5" fill="var(--muted)">${Math.round(v*100)}%</text></g>`).join("");
  const zero=lo<0&&hi>0?`<line x1="${L}" y1="${y(0)}" x2="${W-R}" y2="${y(0)}" stroke="var(--ink)" stroke-opacity=".5" stroke-width="1.8"/>`:"";
  const xTicks=usable.map((p,i)=>{
    const anchor=n===1?"middle":i===0?"start":i===n-1?"end":"middle";
    return`<text x="${x(i).toFixed(1)}" y="${H-16}" text-anchor="${anchor}" font-size="9.5" fill="var(--muted)">${jmShortPeriod(p.period)}</text>`;
  }).join("");
  const overlayLines=showOverlay?overlays.filter(o=>Number.isFinite(o.finalMargin)).map(o=>`<line x1="${L}" y1="${y(o.finalMargin).toFixed(1)}" x2="${W-R}" y2="${y(o.finalMargin).toFixed(1)}" stroke="#B4B2A9" stroke-width="1.1" stroke-dasharray="3 3"><title>${htmlEsc(o.label)} finished at ${pct1(o.finalMargin)}</title></line>`).join(""):"";
  const coords=usable.map((p,i)=>`${x(i).toFixed(1)},${y(p.margin).toFixed(1)}`).join(" ");
  const line=n>1?`<polyline points="${coords}" fill="none" stroke="#3F6FB6" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>`:"";
  const dots=usable.map((p,i)=>{
    const clipped=Math.abs(p.margin)>CLAMP;
    return`<circle cx="${x(i).toFixed(1)}" cy="${y(p.margin).toFixed(1)}" r="4.2" fill="#3F6FB6" stroke="var(--surface)" stroke-width="1.5"><title>${jmShortPeriod(p.period)} · estimated margin ${pct1(p.margin)}${clipped?" (beyond ±100% — likely a data issue; shown at chart edge)":""}</title></circle>`;
  }).join("");
  return`<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Estimated gross margin at each statement">
    ${yTicks}${zero}${overlayLines}${line}${dots}${xTicks}
    <text x="${(L+W-R)/2}" y="${H-3}" text-anchor="middle" font-size="9.5" fill="var(--muted)">statement date</text>
  </svg>`;
}
function jmRailStatus(rep,row,flags,tsByGroup){
  const j=currentJobs(rep)[row]||{};
  const done=j.P!=null&&j.P>=1-1e-6;
  const rowFlags=flags[row]||[];
  const group=rep._consolidatedRows?.[row]?.group;
  const ts=group?tsByGroup.get(group.id):null;
  let color=done?"done":"sage";
  if(!done){
    if(rowFlags.some(f=>f.color==="amber")||(ts&&(ts.materialCost||ts.estimateAnomaly||ts.stalled)))color="amber";
    if(rowFlags.some(f=>f.color==="brick")||(ts&&ts.materialFade))color="brick";
  }
  const flagCount=rowFlags.length+(ts?["materialFade","materialCost","stalled","estimateAnomaly"].filter(k=>ts[k]).length:0);
  return{done,color,flagCount,V:j.V??null,P:j.P??null};
}
function jmDetailHTML(rep,row,state){
  const jobs=currentJobs(rep),j=jobs[row];
  if(!j)return`<p class="empty">No job selected.</p>`;
  const t=rep.table||{};
  const id=(t.job_ids||[])[row]||"",name=(t.job_names||[])[row]||"";
  const title=name||id||j.label||`Row ${row+1}`;
  const sub=name&&id?id:"";
  const{group,points}=jmHistory(rep,row);
  const done=j.P!=null&&j.P>=1-1e-6;
  const flags=(state.flags[row]||[]);
  const ts=group?state.tsByGroup.get(group.id):null;
  const chips=[
    ...flags.map(f=>`<span class="lg ${f.color}">${f.color==="brick"?"\u2691":"\u25B2"} ${f.name}</span>`),
    ts&&ts.materialFade?`<span class="fade-badge" style="margin-left:0">Profit fade</span>`:"",
    ts&&ts.materialCost?`<span class="cost-badge">Cost increase</span>`:"",
    ts&&ts.estimateAnomaly?`<span class="anomaly-badge" title="${htmlEsc(ts.anomalyReason||"")}">${ts.fade>=0?"Profit gain anomaly":"Profit loss anomaly"}</span>`:"",
    ts&&ts.stalled?`<span class="jm-stalled-chip">Stalled</span>`:"",
    done?`<span class="status-pill completed">Completed</span>`:""
  ].filter(Boolean).join("");
  const V=j.V??null,C=j.C??null,B=j.B??null;
  const net=j.net??(j.B!=null&&j.E!=null?j.B-j.E:null);
  const position=V>0&&net!=null?net/V:null;
  const gp=V!=null&&C!=null?V-C:null;
  const leftToBill=V!=null&&B!=null?V-B:null;
  const posSub=net==null?"":net<0?`${sMoney(-net)} underbilled`:net>0?`${sMoney(net)} overbilled`:"balanced";
  const billSub=net==null||net===0?"":net<0
    ?`vs ${sMoney(-net)} underbilled`:`vs ${sMoney(net)} overbilled`;
  const billShort=leftToBill!=null&&net!=null&&net<0&&leftToBill<-net;
  const kpis=`<div class="jm-facts">
    <span class="jm-fact"><span class="lab">Billing position</span><span class="val num${net!=null&&net<0?" neg":""}">${position==null?"—":(position>0?"+":"")+pct1(position)}</span>${posSub?`<span class="sub">${posSub}</span>`:""}</span>
    <span class="jm-fact"><span class="lab">Expected profit/(loss)</span><span class="val num${gp!=null&&gp<0?" neg":""}">${fmtM(gp)}</span><span class="sub">at current estimates</span></span>
    <span class="jm-fact"><span class="lab">Left to bill</span><span class="val num">${fmtM(leftToBill)}</span>${billSub?`<span class="sub${billShort?" neg":""}">${billSub}${billShort?" — does not cover the gap":""}</span>`:""}</span>
  </div>`;
  const overlays=state.overlaysByRow.get(row)||[];
  const overlayToggle=overlays.length?`<label class="jm-overlay-toggle"><input type="checkbox" id="jmOverlay"${state.overlay?" checked":""}> overlay similar jobs (${overlays.length})</label>`:"";
  const charts=`<div class="jm-charts">
    <div class="jm-zone"><div class="jm-chart-head"><p>Billing position by % complete</p>${overlayToggle}</div>
      ${jmBillingChartSVG(points,overlays,state.overlay)}</div>
    <div class="jm-zone"><div class="jm-chart-head"><p>Estimated margin by statement</p></div>
      ${jmMarginChartSVG(points,overlays,state.overlay)}</div>
  </div>`;
  const desc=[...points].sort((a,b)=>String(b.period).localeCompare(String(a.period)));
  const nowPeriod=rep._period&&desc.some(p=>p.period===rep._period)?rep._period:(desc[0]?.period??"");
  const histRows=desc.map(p=>{
    const under=p.net==null?null:-p.net;
    return`<tr class="${p.period===nowPeriod?"now":""}">
      <td>${jmShortPeriod(p.period)}</td>
      <td class="num">${p.P==null?"—":pct0(p.P)}</td>
      <td class="num">${fmt$(p.V)}</td>
      <td class="num">${fmt$(p.C)}</td>
      <td class="num${p.margin!=null&&p.margin<0?" neg":""}">${p.margin==null?"—":pct1(p.margin)}</td>
      <td class="num">${fmt$(p.D)}</td>
      <td class="num">${fmt$(p.B)}</td>
      <td class="num${under!=null&&under>0?" neg":""}">${fmt$(under)}</td>
      <td class="num${p.position!=null&&p.position<0?" neg":""}">${p.position==null?"—":(p.position>0?"+":"")+pct1(p.position)}</td>
    </tr>`;}).join("");
  const fullLink=state.canFullSchedule?`<button class="jm-link" id="jmFullSchedule">Validated WIP \u2192</button>`:"";
  const hist=`<div class="jm-zone" style="padding-bottom:11px"><div class="jm-table-head"><p>Statement history${desc.length===1?"<small>one statement on file</small>":""}</p>${fullLink}</div>
    <table class="jm-hist"><thead><tr><th>Statement</th><th>Complete</th><th>Contract</th><th>Est. cost</th><th>Est. GP%</th><th>Cost to date</th><th>Billed to date</th><th>Under/(over)</th><th>Position</th></tr></thead>
    <tbody>${histRows}</tbody></table></div>`;
  return`<div class="jm-head"><div><div class="jm-title">${htmlEsc(title)}</div>${sub?`<div class="jm-sub">${htmlEsc(sub)}</div>`:""}</div>
      <button class="btn" id="jmClose">Close</button></div>
    <div class="jm-chips">${chips}</div>
    ${kpis}${charts}${hist}`;
}
function openWipModal(rep,focusRow){
  const jobs=currentJobs(rep);
  if(!jobs.length)return;
  const flags=signalRowFlags(rep);
  const tsByGroup=new Map();
  if(BATCH_ANALYSIS_MODE&&MATCH_STATE){
    const trend=timeSeriesData();
    for(const r of (trend?.rows||[]))if(r.groupId)tsByGroup.set(r.groupId,r);
  }
  const labels=tableLabels(rep.table);
  const t=rep.table||{};
  const status=jobs.map((_,row)=>jmRailStatus(rep,row,flags,tsByGroup));
  const order=jobs.map((_,row)=>row).sort((a,b)=>{
    const sa=status[a],sb=status[b];
    const rank=s=>s.done?0:s.color==="brick"?3:s.color==="amber"?2:1;
    return rank(sb)-rank(sa)||(sb.V||0)-(sa.V||0);
  });
  const overlaysByRow=new Map();
  const overlaysFor=row=>{
    if(!overlaysByRow.has(row)){
      const group=rep._consolidatedRows?.[row]?.group||null;
      overlaysByRow.set(row,jmSimilarCompleted(rep,group,jobs[row]?.V??0));
    }
    return overlaysByRow.get(row);
  };
  const canFullSchedule=Boolean(rep._period)||Boolean($("#navConsolidated")&&!$("#navConsolidated").classList.contains("hidden"));
  const state={selected:focusRow!=null&&jobs[focusRow]?focusRow:order[0],
    overlay:false,flags,tsByGroup,overlaysByRow,canFullSchedule};
  const anyFlagged=status.some(s=>!s.done&&s.color!=="sage");
  const railRow=row=>{
    const s=status[row];
    const nm=(t.job_names||[])[row]||(t.job_ids||[])[row]||labels[row]||`Row ${row+1}`;
    const subBits=[s.V!=null?fmtM(s.V):"",s.done?"done":(s.P!=null?pct0(s.P):"")].filter(Boolean).join(" · ");
    return`<button class="jm-row${row===state.selected?" on":""}" data-jmrow="${row}" role="option" aria-selected="${row===state.selected}">
      <span class="jm-dot ${s.color}"></span>
      <span class="jm-row-text"><span class="jm-row-name" style="display:block">${htmlEsc(nm)}</span>
      <span class="jm-row-sub" style="display:block">${htmlEsc(subBits)}${s.flagCount?` · ${s.flagCount} signal${s.flagCount===1?"":"s"}`:""}</span></span>
    </button>`;
  };
  const m=document.createElement("div");m.className="wipmodal job-modal";
  m.innerHTML=`<div class="wm-panel" role="dialog" aria-modal="true" aria-label="Job analysis">
    <div class="jm-body">
      <div class="jm-rail">
        <div class="jm-search"><input type="search" id="jmSearch" placeholder="Search ${jobs.length} job${jobs.length===1?"":"s"}" aria-label="Search jobs"></div>
        <div class="jm-rows" role="listbox" aria-label="Jobs">${order.map(railRow).join("")}</div>
        ${anyFlagged?`<p class="jm-rail-note">Flagged jobs shown first, then by contract value</p>`:""}
      </div>
      <div class="jm-detail" id="jmDetail"></div>
    </div></div>`;
  document.body.appendChild(m);document.body.style.overflow="hidden";
  const close=()=>{m.remove();document.body.style.overflow="";document.removeEventListener("keydown",esc)};
  const esc=e=>{if(e.key!=="Escape")return;
    const open=document.querySelectorAll(".wipmodal");
    if(open[open.length-1]===m)close();};
  document.addEventListener("keydown",esc);
  m.onclick=e=>{if(e.target===m)close()};
  const renderDetail=()=>{
    overlaysFor(state.selected);
    const detail=m.querySelector("#jmDetail");
    detail.innerHTML=jmDetailHTML(rep,state.selected,state);
    detail.scrollTop=0;
    detail.querySelector("#jmClose").onclick=close;
    const overlayBox=detail.querySelector("#jmOverlay");
    if(overlayBox)overlayBox.onchange=()=>{state.overlay=overlayBox.checked;renderDetail();};
    const full=detail.querySelector("#jmFullSchedule");
    if(full)full.onclick=()=>{
      if(rep._period)openConsolidatedModal(rep._period);
      else{close();$("#navConsolidated")?.click();}
    };
  };
  const select=row=>{
    state.selected=row;
    m.querySelectorAll(".jm-row").forEach(b=>{
      const on=+b.dataset.jmrow===row;
      b.classList.toggle("on",on);b.setAttribute("aria-selected",on);
    });
    renderDetail();
  };
  m.querySelectorAll(".jm-row").forEach(b=>b.onclick=()=>select(+b.dataset.jmrow));
  const search=m.querySelector("#jmSearch");
  search.oninput=()=>{
    const q=search.value.trim().toLowerCase();
    m.querySelectorAll(".jm-row").forEach(b=>{
      const row=+b.dataset.jmrow;
      const hay=`${(t.job_names||[])[row]||""} ${(t.job_ids||[])[row]||""} ${labels[row]||""}`.toLowerCase();
      b.style.display=!q||hay.includes(q)?"":"none";
    });
  };
  renderDetail();
  const selectedBtn=m.querySelector(`.jm-row[data-jmrow="${state.selected}"]`);
  if(selectedBtn)selectedBtn.scrollIntoView({block:"nearest"});
}

function sourceFinding(rep,c){
  const fs=rep.findings||[];
  return fs.find(f=>f.row_index===c.row&&f.culprit_column===c.col)
    ||fs.find(f=>f.row_label===c.label&&f.proposed_correction===c.implied)
    ||null;
}
function sourceUrlAtPage(page){
  if(!SOURCE_URL)return"";
  return SOURCE_KIND==="pdf"?`${SOURCE_URL}#page=${page||1}&zoom=page-width`:SOURCE_URL;
}
function openSourceReview(rep,focusCi=0){
  const corrs=(rep.analysis||{}).corrections||[];
  if(!corrs.length)return;
  focusCi=Math.max(0,Math.min(focusCi,corrs.length-1));
  const itemHTML=corrs.map((c,ci)=>{
    const f=sourceFinding(rep,c),page=f?.page||1;
    const col=(rep.table?.columns?.[c.col]||{}).header||variableName(c.variable);
    return `<div class="sr-edit${ci===focusCi?" active":""}" role="button" tabindex="0" data-ci="${ci}" data-page="${page}">
      <span class="sr-edit-top"><span class="sr-edit-job">${htmlEsc(c.label||`Row ${c.row+1}`)}</span>${canReviewSource()?`<span class="sr-page">first on p. ${page}</span>`:""}</span>
      <span class="sr-edit-col">${htmlEsc(col)}</span>
      <span class="sr-values"><span class="from">$${fmt$(c.printed)}</span><span aria-hidden="true">\u2192</span><span class="to">$${fmt$(c.implied)}</span></span>
      <span class="sr-apply"><span>${c.checks} independent check${c.checks===1?"":"s"}${c.corroborated?" + totals":""}</span>
        <label><input type="checkbox" class="sr-check" data-ci="${ci}" ${ACCEPTED.has(ci)?"checked":""}> Applied</label></span>
    </div>`;
  }).join("");
  const initialPage=sourceFinding(rep,corrs[focusCi])?.page||1;
  const viewer=SOURCE_KIND==="pdf"
    ?`<iframe class="sr-frame" id="srDocument" title="Original uploaded PDF" src="${sourceUrlAtPage(initialPage)}"></iframe>`
    :SOURCE_KIND==="image"
      ?`<div class="sr-image-wrap"><img class="sr-image" src="${SOURCE_URL}" alt="Original uploaded schedule"></div>`
      :`<div class="sr-empty"><div><strong>${SOURCE_FILE?"No page preview for this file type":"No original PDF in the sample"}</strong><p>${SOURCE_FILE?"Upload a PDF or image to review corrections beside the source.":"The sample is generated in memory. Upload a PDF or image to test page-by-page source review."}</p></div></div>`;
  const sourceHelp=canReviewSource()
    ?"Select a job or edit to open the first page where that job appears. Uncheck it to keep the printed value."
    :SOURCE_FILE
      ?"This file has no page preview. You can still review and reverse every correction here."
      :"The sample has no attached PDF. You can still review and reverse every correction here.";
  const m=document.createElement("div");m.className="wipmodal source-review";
  m.innerHTML=`<div class="wm-panel" role="dialog" aria-modal="true" aria-labelledby="srTitle">
    <div class="wm-head"><h3 id="srTitle">Source review <small>${htmlEsc(SOURCE_FILE?.name||"generated sample")}</small></h3>
      <div class="wm-legend"><span class="lg brick">printed</span><span class="lg sage">suggested</span></div>
      <button class="btn" id="srClose">Close</button></div>
    <div class="sr-body"><div class="sr-viewer">${viewer}</div>
      <aside class="sr-side"><div class="sr-side-head"><p class="eyebrow">Edits</p><h4>${corrs.length} suggested correction${corrs.length===1?"":"s"}</h4>
        <p>${sourceHelp}</p></div>
        <div class="sr-edits">${itemHTML}</div></aside></div></div>`;
  document.body.appendChild(m);document.body.style.overflow="hidden";
  const close=()=>{m.remove();document.body.style.overflow="";document.removeEventListener("keydown",esc);
    if(VIEW==="dash")renderDash(REPORT);else renderCertificate(REPORT);};
  const esc=e=>{if(e.key==="Escape")close()};
  const select=ci=>{
    m.querySelectorAll(".sr-edit").forEach(x=>x.classList.toggle("active",+x.dataset.ci===ci));
    const page=sourceFinding(rep,corrs[ci])?.page||1;
    const frame=m.querySelector("#srDocument");
    if(frame)frame.src=sourceUrlAtPage(page);
  };
  document.addEventListener("keydown",esc);
  m.onclick=e=>{if(e.target===m)close()};
  m.querySelector("#srClose").onclick=close;
  m.querySelectorAll(".sr-edit").forEach(b=>b.onclick=e=>{
    if(e.target.closest(".sr-check"))return;
    select(+b.dataset.ci);
  });
  m.querySelectorAll(".sr-edit").forEach(b=>b.onkeydown=e=>{
    if((e.key==="Enter"||e.key===" ")&&!e.target.closest(".sr-check")){
      e.preventDefault();select(+b.dataset.ci);
    }
  });
  m.querySelectorAll(".sr-check").forEach(box=>box.onchange=()=>{
    const ci=+box.dataset.ci;
    if(box.checked)ACCEPTED.add(ci);else ACCEPTED.delete(ci);
  });
}
function tableHTML(rep,idp="wiprow"){
  const t=rep.table;if(!t)return"";
  const values=tableValues(t),labels=tableLabels(t);
  const corr={};(rep.analysis?.corrections_applied||[]).forEach(c=>corr[c.row+"_"+c.col]=c);
  const flags=signalRowFlags(rep);
  const pct=t.columns.map(c=>["P","PB","M"].includes(c.variable));
  const head=`<tr><th>Job</th>${t.columns.map(c=>`<th>${htmlEsc(c.header||c.name||c.variable||"")}${c.variable_name?`<span class="vn">${htmlEsc(c.variable_name)}</span>`:""}</th>`).join("")}</tr>`;
  const body=values.map((row,i)=>{
    const rf=flags[i]||[];
    const worst=rf.some(f=>f.color==="brick")?"brick":rf.length?"amber":"";
    const mark={};rf.forEach(f=>f.vars.forEach(v=>{if(mark[v]!=="brick")mark[v]=f.color}));
    const names=rf.map(f=>f.name).join(", ");
    const icons=[...new Map(rf.map(f=>[f.name,f])).values()]
      .map(f=>`<span class="sigicon ${f.color}" title="${f.name}">${f.color==="brick"?"\u2691":"\u25B2"}</span>`).join("");
    return `<tr id="${idp}-${i}"${worst?` class="sigrow-${worst}" title="${names}"`:""}><td>${icons?`<span class="sigicons">${icons}</span>`:""}${htmlEsc(labels[i]??"")}</td>${row.map((x,j)=>{
    const c=corr[i+"_"+j];
    const v=t.columns[j]&&t.columns[j].variable;
    const mk=v&&mark[v]?` sigcell-${mark[v]}`:"";
    const txt=x==null?"—":pct[j]?(100*x).toFixed(1)+"%":fmt$(x);
    return c?`<td class="flag num${mk}" title="document shows ${fmt$(c.observed)}; identities imply ${fmt$(c.used)}">${fmt$(c.observed)}</td>`
            :`<td class="num${mk}">${txt}</td>`}).join("")}</tr>`}).join("");
  const sums=t.columns.map((c,j)=>pct[j]?"":fmt$(values.reduce((s,r)=>s+(r[j]??0),0)));
  const foot=`<tr><td>Total</td>${sums.map(s=>`<td class="num">${s}</td>`).join("")}</tr>`;
  return `<table><thead>${head}</thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table>`;
}


