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
  if(!APP_STATE.batch.matchState)return[];
  const done=APP_STATE.batch.matchState.groups.filter(g=>g!==group&&jmIsCompletedGroup(g))
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
  if(APP_STATE.batch.analysisMode&&APP_STATE.batch.matchState){
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

