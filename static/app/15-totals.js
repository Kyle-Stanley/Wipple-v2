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
function totalsDetail(rep,accepted=APP_STATE.document.accepted){
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
