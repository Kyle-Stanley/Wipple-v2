const PRINT_COLUMN_ORDER=["V","C","G","D","P","E","B","U","O","Q","R","RB","M","PB","H","N"];
const PRINT_COLUMN_NAMES={
  V:"Contract Value",C:"Est. Cost",G:"Est. GP",D:"Cost to Date",
  P:"% Complete",E:"Earned Revenue",B:"Billings to Date",
  U:"Underbillings",O:"Overbillings",Q:"Cost to Complete",
  R:"Backlog",RB:"Remaining Billings",M:"GP %",PB:"% Billed",
  H:"Earned GP to Date",N:"Net Billing Position"
};
const COMPACT_COLUMN_NAMES={
  V:"Contract<br>Value",C:"Est.<br>Cost",G:"Est.<br>GP",D:"Cost to<br>Date",
  P:"%<br>Complete",E:"Earned<br>Revenue",B:"Billings<br>to Date",
  U:"Under-<br>billings",O:"Over-<br>billings",Q:"Cost to<br>Complete",
  R:"Backlog",RB:"Remaining<br>Billings",M:"GP %",PB:"% Billed",
  H:"Earned GP<br>to Date",N:"Net Billing<br>Position"
};
const htmlEsc=s=>String(s??"").replace(/[&<>"']/g,ch=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[ch]));

function printSignalTitle(s){
  const n=s.count??(s.jobs||[]).length;
  const jobs=`${n} job${n===1?"":"s"}`;
  const titles={
    trapped_cash:"Significant under billings with limited time to recover",
    job_borrow:`${jobs} with possible job borrow`,
    loss_jobs:`${jobs} losing money`,
    cost_overrun:`${jobs} over estimated cost`,
    early_concentration:"Significant contract value remains in early-stage work",
    margin_outlier:`${jobs} with unusually high estimated margins`,
    thin_margin_backlog:"Significant remaining work carries thin margins",
    job_concentration:"Program value is concentrated in a single job",
    unrecognized_fade:`${jobs} earning below the stated final margin`
  };
  return titles[s.id]||s.headline;
}

function printSignalWhy(s){
  const why={
    trapped_cash:"Late-stage underbillings can indicate unapproved change orders or receivables that may not convert to cash.",
    job_borrow:"Billings exceed cost incurred plus projected profit, so part of the remaining job has already been converted to cash.",
    loss_jobs:"Expected job losses reduce the contractor's net worth and should be recognized immediately.",
    cost_overrun:"Costs above the current estimate indicate that the estimate is stale and reported profit may be overstated.",
    early_concentration:"Early-stage margins are less proven and carry greater estimate risk.",
    margin_outlier:"Margins far above the contractor's own norm may indicate unrecognized profit fade.",
    thin_margin_backlog:"Thin-margin work has no cushion; small overruns flip it to losses.",
    job_concentration:"The program's outcome is tied to a single job's performance.",
    unrecognized_fade:"The remaining work must out-earn the work completed so far for the stated margin to hold."
  };
  return why[s.id]||s.why||"";
}

function printScheduleTable(rep){
  const t=rep.table;
  if(!t)return'<p class="empty-print">No standardized schedule is available.</p>';

  const cols=(t.columns||[]).map((c,index)=>({...c,index}))
    .filter(c=>c.variable&&PRINT_COLUMN_ORDER.includes(c.variable))
    .sort((a,b)=>PRINT_COLUMN_ORDER.indexOf(a.variable)-PRINT_COLUMN_ORDER.indexOf(b.variable));

  if(!cols.length)return'<p class="empty-print">No mapped WIP columns are available.</p>';

  const vals=tableValues(t).map(row=>[...row]);
  const labels=tableLabels(t);
  const corrs=(rep.analysis||{}).corrections||[];
  corrs.forEach((c,ci)=>{
    if(APP_STATE.document.accepted.has(ci)&&vals[c.row])vals[c.row][c.col]=c.implied;
  });

  const isPct=v=>["P","PB","M"].includes(v);
  const cell=(x,v)=>{
    if(x==null||!Number.isFinite(+x))return"—";
    return isPct(v)?(100*(+x)).toFixed(1)+"%":fmt$(+x);
  };

  const head=`<tr><th class="jobcol">Job</th>${cols.map(c=>
    `<th>${htmlEsc(PRINT_COLUMN_NAMES[c.variable]||c.variable_name||c.header||c.variable)}</th>`
  ).join("")}</tr>`;

  const body=vals.map((row,i)=>`<tr>
    <td class="jobcol">${htmlEsc(labels[i]??"")}</td>
    ${cols.map(c=>`<td>${cell(row[c.index],c.variable)}</td>`).join("")}
  </tr>`).join("");

  const foot=`<tr><td class="jobcol">Total</td>${cols.map(c=>{
    if(isPct(c.variable))return"<td></td>";
    const sum=vals.reduce((s,row)=>s+(Number.isFinite(+row[c.index])?+row[c.index]:0),0);
    return`<td>${fmt$(sum)}</td>`;
  }).join("")}</tr>`;

  return`<table class="print-wip"><thead>${head}</thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table>`;
}

function printInFrame(reportHtml){
  const oldFrame=document.getElementById("wipple-print-frame");
  if(oldFrame)oldFrame.remove();
  const frame=document.createElement("iframe");
  frame.id="wipple-print-frame";frame.setAttribute("aria-hidden","true");
  Object.assign(frame.style,{position:"fixed",right:"0",bottom:"0",width:"0",
    height:"0",border:"0",visibility:"hidden"});
  document.body.appendChild(frame);
  const doc=frame.contentDocument||frame.contentWindow.document;
  doc.open();doc.write(reportHtml);doc.close();
  const printFrame=()=>{
    try{
      doc.title="\u200B";
      frame.contentWindow.focus();
      frame.contentWindow.print();
    }finally{setTimeout(()=>frame.remove(),30000);}
  };
  if(doc.readyState==="complete")setTimeout(printFrame,100);
  else frame.onload=()=>setTimeout(printFrame,100);
}

function printConsolidated(period){
  const rows=consolidatedRows(period);
  const cols=PRINT_COLUMN_ORDER.map(key=>[key,PRINT_COLUMN_NAMES[key]]);
  const pct=new Set(["P","PB","M"]);
  const cell=(value,key)=>value==null?"—":pct.has(key)
    ?(100*value).toFixed(1)+"%":fmt$(value);
  const rowHTML=r=>`<tr><td>${htmlEsc(r.id||"—")}</td>
      <td class="job">${htmlEsc(r.name||"—")}${r.deduplicated?"*":""}</td>
      ${cols.map(([key])=>`<td>${cell(r.vars[key],key)}</td>`).join("")}</tr>`;
  const totalLine=(label,selected)=>`<tr class="total"><td colspan="2">${label}</td>${cols.map(([key])=>
    `<td>${pct.has(key)?"":cell(selected.reduce((sum,r)=>sum+(r.vars[key]||0),0),key)}</td>`).join("")}</tr>`;
  const sections=[
    ["Work in progress",rows.filter(r=>r.type!=="cc"),"WIP total"],
    ["Completed contracts",rows.filter(r=>r.type==="cc"),"CC total"]
  ].filter(([,selected])=>selected.length);
  const showCombined=sections.length===2;
  const body=sections.map(([label,selected,total])=>
    `<tr class="group"><td colspan="${cols.length+2}">${label}</td></tr>
      ${selected.map(rowHTML).join("")}${totalLine(total,selected)}`).join("");
  const hasDedupe=rows.some(r=>r.deduplicated);
  const reportHtml=`<!doctype html><html lang="en"><head><meta charset="utf-8">
    <title>Validated WIP · ${htmlEsc(displayPeriod(period))}</title><style>
    :root{--ink:#20251F;--muted:#687064;--line:#D8D5CC;--soft:#F4F2EC}
    *{box-sizing:border-box}html,body{margin:0;color:var(--ink);font-family:Arial,sans-serif}
    @page{size:letter landscape;margin:.32in}body{padding:.08in}
    header{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:14px}
    h1{font:500 23px Georgia,serif;margin:0}header p{font-size:10px;color:var(--muted);margin:3px 0 0}
    .meta{text-align:right;font-size:9px;color:var(--muted);line-height:1.45}
    table{width:100%;border-collapse:collapse;font-size:6.4px;font-variant-numeric:tabular-nums}
    thead{display:table-header-group}tfoot{display:table-footer-group}tr{break-inside:avoid}
    th{background:var(--soft);color:var(--muted);font-size:6px;text-align:right;padding:4px 3px;border:1px solid var(--line);white-space:normal}
    td{padding:3px;border:1px solid #e2e0da;text-align:right;white-space:nowrap}
    th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
    .job{min-width:1.25in;max-width:1.7in;white-space:normal}
    .group td{background:var(--soft);color:var(--muted);font-weight:600;letter-spacing:.05em;text-transform:uppercase;text-align:left}
    .total td{font-weight:600;border-top:1.5px solid var(--ink)}
    .note{font-size:8px;color:var(--muted);margin-top:9px}
    </style></head><body><header><div><h1>Validated WIP</h1>
      <p>Combined WIP and completed-contract schedule · ${htmlEsc(displayPeriod(period))}</p></div>
      <div class="meta">${rows.length} jobs<br>${new Date().toLocaleString()}</div></header>
    <table><thead><tr><th>Job ID</th><th>Job name</th>
      ${cols.map(([,label])=>`<th>${label}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody>${showCombined?`<tfoot>${totalLine("Combined total",rows)}</tfoot>`:""}</table>
    ${hasDedupe?'<p class="note">* Completed-contract values replace the same-period WIP duplicate.</p>':""}
    </body></html>`;
  printInFrame(reportHtml);
}

function printSummary(rep){
  const a=rep.analysis||{};
  const k=computeKpis(rep);
  const isCC=a.schema==="cc";
  const manual=rep.overall_status==="user_mapped_unverified";
  const kpis=isCC?[
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

  const signals=computeSignals(rep);
  const signalHtml=signals.length?signals.map(s=>{
    const tier=s.severity>=.66?"high":s.severity>=.33?"med":"low";
    const cols=(s.cols&&s.cols.length)?s.cols:["Detail"];
    const rows=(s.jobs||[]).map(j=>{
      const cells=(j.cells&&j.cells.length)?j.cells:[j.detail||"—"];
      return`<tr><td>${htmlEsc(j.label)}</td>${cells.map(c=>
        `<td>${htmlEsc(c)}</td>`).join("")}</tr>`;
    }).join("");
    return`<section class="print-signal">
      <div class="signal-head">
        <div class="signal-title">
          <span class="print-severity ${tier}">${tier}</span>
          <h3>${htmlEsc(printSignalTitle(s))}</h3>
        </div>
        ${s.dollars!=null?`<span class="signal-amount">${htmlEsc(fmtM(s.dollars))}</span>`:""}
      </div>
      ${rows?`<table class="print-signal-table"><thead><tr><th>Job</th>${
        cols.map(c=>`<th>${htmlEsc(c)}</th>`).join("")
      }</tr></thead><tbody>${rows}</tbody></table>`:""}
    </section>`;
  }).join(""):'<p class="empty-print">No underwriting signals exceeded the reporting threshold.</p>';
  const trends=APP_STATE.batch.analysisMode?timeSeriesData():null;
  const printEub=APP_STATE.batch.analysisMode?earlyUnderbillingFadeData():null;
  const printJobBorrow=signals.find(s=>s.id==="job_borrow")||null;
  const trendStrip=(trends||printJobBorrow)?`<section class="print-trends" style="margin-top:0">
    <h2 class="section-title">Across reporting periods</h2>
    <p class="section-sub">Cross-period flags include profit fade, cost changes, job borrow, and estimate anomalies. Anomalies combine fixed materiality thresholds with robust comparison to the contractor’s linked job history.</p>
    <div class="print-trend-kpis">
      <div class="print-trend-kpi fade"><span>Profit fade</span><strong>${htmlEsc(fmtM(trends?.fade||0))}</strong>
        <small>${trends?.fadeJobs||0} job${(trends?.fadeJobs||0)===1?"":"s"} lost expected profit</small></div>
      <div class="print-trend-kpi"><span>Cost estimate increases</span><strong>${htmlEsc(fmtM(trends?.totalCostIncrease||0))}</strong>
        <small>${trends?.costIncreaseJobs||0} job${(trends?.costIncreaseJobs||0)===1?"":"s"} revised upward</small></div>
      <div class="print-trend-kpi${printJobBorrow?" borrow":""}"><span>Job borrow</span><strong>${htmlEsc(fmtM(printJobBorrow?.dollars||0))}</strong>
        <small>${printJobBorrow
          ?`${printJobBorrow.count} job${printJobBorrow.count===1?"":"s"} have billings pulled forward`
          :"No billings pulled forward beyond cost and projected profit"}</small></div>
      <div class="print-trend-kpi${trends?.anomalyJobs?" anomaly":""}"><span>Estimate anomalies</span><strong>${trends?.anomalyJobs||0}</strong>
        <small>${trends?.largestAnomaly
          ?`Largest GP swing ${htmlEsc(fmtM(Math.abs(trends.largestAnomaly.fade)))}`
          :"No extreme cross-period estimate changes"}</small></div>
    </div>
    ${eubSentenceHTML(printEub,false)}
    </section>`:"";
  const trendTable=trends?.rows?.length?`<section class="print-trends">
    <h2 class="section-title">Jobs with changes between statements</h2>
    ${`<table><thead><tr><th>Job</th><th>Signal</th><th>Periods</th><th>Prior GP</th><th>Latest GP</th><th>GP change</th><th>Margin change</th></tr></thead>
      <tbody>${trends.rows.map(row=>{const flags=[
        row.estimateAnomaly?`<span class="estimate-anomaly-badge">${row.fade>=0?"Profit gain anomaly":"Profit loss anomaly"}</span>`:"",
        row.materialFade?'<span class="profit-fade-badge">Profit fade</span>':"",
        row.materialCost?"Cost increase":"",
        row.stalled?"Stalled":""
      ].filter(Boolean).join(" · ");return`<tr class="${row.estimateAnomaly?"estimate-anomaly-row":row.materialFade?"profit-fade-row":""}"><td>${htmlEsc(row.name||row.id||"—")}</td>
        <td>${flags||"—"}</td>
        <td>${htmlEsc(`${row.from.slice(0,4)} → ${row.to.slice(0,4)}`)}</td>
        <td>${htmlEsc(row.priorProfit==null?"—":fmtM(row.priorProfit))}</td>
        <td>${htmlEsc(row.currentProfit==null?"—":fmtM(row.currentProfit))}</td>
        <td class="${row.materialFade?"fade-value":""}">${htmlEsc(row.fade==null?"—":fmtM(row.fade))}</td>
        <td>${htmlEsc(row.marginChange==null?"—":`${row.marginChange>=0?"+":""}${pct1(row.marginChange)}`)}</td></tr>`}).join("")}</tbody></table>`}
    </section>`:"";

  const corrections=(a.corrections||[]).filter((_,i)=>APP_STATE.document.accepted.has(i)).length;
  const jobCount=tableJobCount(rep.table);

  const reportHtml=`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>&#8203;</title>
<style>
:root{
  --ink:#20251F;--muted:#687064;--line:#D8D5CC;--soft:#F4F2EC;
  --sage:#3A4C3E;--brick:#A3402F;--amber:#92671E;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;color:var(--ink);font-family:Arial,Helvetica,sans-serif}
@page summary{size:letter portrait;margin:.45in}
@page schedule{size:letter landscape;margin:.3in}
.summary-page{page:summary}
.chart-block{page:summary;break-inside:avoid;page-break-inside:avoid;margin-top:32px}
.schedule-page{page:schedule;break-before:page}
.report-head{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:14px}
.report-head h1{font-family:Georgia,serif;font-size:22px;font-weight:500;margin:0}
.report-head p{font-size:10px;color:var(--muted);margin:3px 0 0}
.report-meta{text-align:right;font-size:9px;color:var(--muted);line-height:1.5}
.print-kpis{display:grid;grid-template-columns:repeat(${kpis.length},1fr);gap:7px;margin:0 0 26px}
.print-kpi{border:1px solid var(--line);border-radius:6px;padding:9px 8px;min-width:0}
.print-kpi .label{font-size:8px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);line-height:1.25;min-height:20px}
.print-kpi .value{font-size:17px;font-weight:600;margin-top:4px;white-space:nowrap}
.section-title{font-family:Georgia,serif;font-size:17px;font-weight:500;margin:0 0 5px}
.section-sub{font-size:10px;color:var(--muted);margin:0 0 14px}
.print-signal{border-top:1px solid var(--line);padding:12px 0 14px;break-inside:avoid}
.signal-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:6px}
.signal-title{display:flex;align-items:center;gap:7px;min-width:0}
.signal-head h3{font-size:11px;margin:0;font-weight:600}
.signal-amount{font-size:11px;font-weight:700;white-space:nowrap}
.print-severity{font-size:7px;font-weight:700;letter-spacing:.04em;text-transform:lowercase;border-radius:4px;padding:2px 6px;flex:none}
.print-severity.high{background:#F2DDD6;color:var(--brick)}
.print-severity.med{background:#F2E7CF;color:var(--amber)}
.print-severity.low{background:#E4E9E1;color:var(--sage)}
.print-signal-table{width:100%;border-collapse:collapse;table-layout:auto;font-size:8.5px;font-variant-numeric:tabular-nums}
.print-signal-table th{font-size:7.5px;font-weight:500;color:var(--muted);padding:3px 6px 4px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
.print-signal-table td{padding:4px 6px;border-bottom:1px solid #E8E5DD;text-align:right;white-space:nowrap}
.print-signal-table th:first-child,.print-signal-table td:first-child{text-align:left;padding-left:0;white-space:normal;width:34%}
.print-signal-table th:last-child,.print-signal-table td:last-child{padding-right:0}
.print-signal-table tbody tr:last-child td{border-bottom:0}
.print-signal + .print-signal{margin-top:2px}
.print-trends{margin-top:24px;break-inside:avoid}
.print-trend-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:0 0 12px}
.print-trend-kpi{border:1px solid var(--line);border-radius:6px;padding:8px}
.print-trend-kpi.fade{background:#FBF0EC;border-color:#D8AA9D}
.print-trend-kpi span{display:block;color:var(--muted);font-size:8px;letter-spacing:.07em;text-transform:uppercase}
.print-trend-kpi strong{display:block;font-size:17px;margin-top:2px}
.print-trend-kpi.fade strong{color:var(--brick)}
.print-trend-kpi.borrow{background:#FAF5E8;border-color:#D9C28F}
.print-trend-kpi.borrow strong{color:var(--amber)}
.print-trend-kpi.anomaly{background:#FAF5E8;border-color:#D9C28F}
.print-trend-kpi.anomaly strong{color:var(--amber)}
.print-trend-kpi small{display:block;color:var(--muted);font-size:8px;margin-top:1px}
.print-trends table{width:100%;border-collapse:collapse;font-size:9px}
.print-trends th,.print-trends td{padding:5px 6px;border-bottom:1px solid var(--line);text-align:right}
.print-trends th:first-child,.print-trends td:first-child{text-align:left}
.print-trends .profit-fade-row td{background:#FFF8F5}
.print-trends .estimate-anomaly-row td{background:#FFFCF5}
.profit-fade-badge,.estimate-anomaly-badge{display:inline-block;border-radius:999px;padding:1px 5px;font-size:7px;font-weight:700;text-transform:uppercase}
.profit-fade-badge{background:#F2DDD6;color:var(--brick)}
.estimate-anomaly-badge{background:#F2E7CF;color:var(--amber)}
.print-trends .fade-value{color:var(--brick);font-weight:700}
.chart-block h2,.schedule-page h2{font-family:Georgia,serif;font-size:18px;font-weight:500;margin:0 0 3px}
.chart-block .sub,.schedule-page .sub{font-size:10px;color:var(--muted);margin:0 0 12px}
.chart-block .card{border:0;padding:0;margin:0;background:white;break-inside:avoid;page-break-inside:avoid}
.chart-block .card h3{display:none}
.chart-block svg{margin-top:10px!important;max-height:3.2in}
.chart-block .card p{font-size:10px!important;line-height:1.45;margin-top:8px!important}
.empty-print{font-size:10px;color:var(--muted)}
.eub-note{font-size:9px;background:#F6F4EE;border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:0 0 6px;line-height:1.5}
.print-trends+.section-title,.print-trends~.section-title{margin-top:18px}
.print-wip{border-collapse:collapse;width:100%;table-layout:auto;font-size:7px;font-variant-numeric:tabular-nums}
.print-wip thead{display:table-header-group}
.print-wip tfoot{display:table-footer-group}
.print-wip tr{break-inside:avoid}
.print-wip th{background:var(--soft);font-size:6.5px;font-weight:600;text-align:right;padding:4px 4px;border:1px solid var(--line);white-space:normal;line-height:1.15}
.print-wip td{padding:3px 4px;border:1px solid #e2e0da;text-align:right;white-space:nowrap}
.print-wip .jobcol{text-align:left;min-width:1.35in;max-width:1.8in;white-space:normal}
.print-wip tfoot td{font-weight:600;border-top:1.5px solid var(--ink)}
.schedule-note{font-size:8px;color:var(--muted);margin:8px 0 0}
@media print{
  .summary-page,.chart-block,.schedule-page{display:block}
}
</style>
</head>
<body>
<section class="summary-page">
  <header class="report-head">
    <div>
      <h1>Underwriting report</h1>
      <p>${manual?"Reviewed column mapping":"Validated WIP schedule"}</p>
    </div>
    <div class="report-meta">
      ${jobCount} jobs · ${corrections} correction${corrections===1?"":"s"} applied
    </div>
  </header>
  <div class="print-kpis">${kpis.map(([label,value])=>
    `<div class="print-kpi"><div class="label">${htmlEsc(label)}</div><div class="value">${htmlEsc(value)}</div></div>`
  ).join("")}</div>
  ${trendStrip}
  <h2 class="section-title">Underwriting signals</h2>
  <p class="section-sub">Jobs and values behind each underwriting flag.</p>
  ${signalHtml}
  ${trendTable}
</section>

<section class="chart-block">
  <h2>Billing position by completion</h2>
  <p class="sub">Each dot is a job, sized by contract value.</p>
  ${bookShape(rep)}
</section>

<section class="schedule-page">
  <h2>Standardized WIP schedule</h2>
  <p class="sub">${jobCount} jobs · ${manual?"reviewed column assignments":"mathematically identified columns"} shown in a consistent order</p>
  ${printScheduleTable(rep)}
  <p class="schedule-note">Supplemental or unidentified source columns are omitted from this standardized view. Applied reviewer corrections are reflected.</p>
</section>
</body>
</html>`;

  printInFrame(reportHtml);
}

function downloadCSV(rep){
  const t=rep.table;if(!t)return;
  const esc=s=>`"${String(s).replace(/"/g,'""')}"`;
  const corrs=(rep.analysis||{}).corrections||[];
  const vals=tableValues(t).map(r=>[...r]);
  const labels=tableLabels(t);
  const applied=[];
  corrs.forEach((c,ci)=>{
    if(!APP_STATE.document.accepted.has(ci))return;
    vals[c.row][c.col]=c.implied;applied.push(c);
  });
  const lines=[["Job",...t.columns.map(c=>c.header||c.name||c.variable||"")].map(esc).join(","),
    ...vals.map((r,i)=>[labels[i]??"",...r.map(x=>x==null?"":x)].map(esc).join(","))];
  if(applied.length){
    lines.push("");
    lines.push(esc("Corrections included in reviewed report:"));
    for(const c of applied)lines.push([c.label,(t.columns[c.col]||{}).header||"",
      "printed "+c.printed,"corrected to "+c.implied].map(esc).join(","));
  }
  const blob=new Blob([lines.join("\n")],{type:"text/csv"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download=(rep.source||"wip")+".validated.csv";a.click();URL.revokeObjectURL(a.href);
}