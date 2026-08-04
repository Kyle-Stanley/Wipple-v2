function computeValidationChecks(rep,accepted){
  const v=rep.validator_status,o=rep.overall_status;
  const findings=rep.findings||[];
  const witnesses=rep.witnesses||[];
  const pr=rep.parse||{};
  const td=totalsDetail(rep,accepted);
  const repairs=(pr.cell_flags||[]).filter(f=>f.flag==="confusable_repair").length;
  const nrows=tableJobCount(rep.table)||pr.n_rows||0;
  const checks=[];
  const C=(st,label,note)=>checks.push({st,label,note});

  C("ok",`Parsed ${nrows} jobs, ${pr.n_numeric_cols??"?"} numeric columns`,
    repairs?`${repairs} OCR-damaged cell${repairs>1?"s":""} repaired, originals kept`:"");
  if(o==="verified"||o==="verified_mapping_with_findings"){
    const cov=(rep.analysis||{}).coverage||{};
    const partial=cov.numeric_cols&&cov.mapped_cols<cov.numeric_cols;
    C("ok","Column identification certified",
      `${partial?`${cov.mapped_cols} of ${cov.numeric_cols} numeric columns identified; the remaining columns were not needed to validate the WIP. `:""}${witnesses.length} accounting identit${witnesses.length===1?"y":"ies"} verified across the rows. Headers were not used.`);
  }else if(o==="disambiguated"){
    C("warn","Column identification certified, with one tie",
      "The numbers allowed two readings. Column headers decided between them.");
  }else if(o==="user_mapped_unverified"){
    C("warn","Column mapping reviewed",
      "Too little numeric structure for mathematical validation. The displayed column assignments were used for calculation.");
  }else{
    C("warn","Column identification not certifiable",
      o==="unmapped"
        ?"Too little numeric structure for mathematical validation. The column headers were not clear enough to match."
        :"Too little numeric structure for mathematical validation. Columns were matched from their headers.");
  }
  const corrsC=(rep.analysis||{}).corrections||[];
  const corrIndexFor=f=>corrsC.findIndex(x=>x.label===f.row_label&&x.implied===f.proposed_correction);
  const findingsFixed=findings.length>0&&findings.every(f=>{const ci=corrIndexFor(f);return ci>=0&&accepted.has(ci);});
  if(v==="success"||v==="validation_failed"){
    C(findings.length?(findingsFixed?"fixed":"bad"):"ok",
      findings.length
        ?findingsFixed
          ?`${findings.length} cell${findings.length>1?"s":""} failed the row identities as printed — corrected`
          :`${findings.length} cell${findings.length>1?"s fail":" fails"} the row identities`
        :"Every job satisfies the row identities",
      findings.length
        ?findingsFixed
          ?"Every failing cell has an applied correction below; revert any row to keep the printed value."
          :"Detailed below."
        :"");
  }
  if(td&&td.present){
    const scopeNote=totalsScopeNote(td);
    const totalFixed=(td.totalCorrections||[]).filter(t=>t.accepted);
    if(td.allMatch)C("ok","Stated totals match the validated column sums",scopeNote);
    else if(td.allExplained){
      const detail=totalFixed.map(t=>`${t.header}: ${fmtM(t.stated)} → ${fmtM(t.proposedCorrection)}`).join("; ");
      C("fixed",totalFixed.length
          ?`${totalFixed.length} stated total${totalFixed.length===1?" was":"s were"} incorrect — corrected`
          :"Stated totals match after the job corrections",
        `${scopeNote}${scopeNote&&detail?" ":""}${detail||"The stated totals agree with the independently validated job values."}`);
    }else{
      const unexplained=(td.mismatches||[]).filter(m=>!m.explained);
      const colDetail=unexplained.map(m=>`${m.header}: stated ${fmtM(m.stated)} vs validated rows ${fmtM(m.computed)} (Δ ${fmtM(m.diff)})`).join("; ");
      const conflicts=unexplained.some(m=>m.status==="conflicts_with_job_corrections");
      const unresolved=unexplained.some(m=>m.status==="unassessed");
      C("bad",unresolved?"One or more validated totals could not be assessed":
          conflicts?"A stated total conflicts with the independently proven job corrections":
          "Stated totals disagree with the validated column sums",
        `${scopeNote}${scopeNote&&colDetail?" ":""}${colDetail}`);
    }
  }

  (rep._extra||[]).forEach(x=>C(x.st,x.label,x.note));
  const nBad=checks.filter(c=>c.st==="bad").length;
  const nFixed=checks.filter(c=>c.st==="fixed").length;
  const nWarn=checks.filter(c=>c.st==="warn").length;
  const passed=checks.filter(c=>c.st==="ok"||c.st==="fixed").length;
  const head=nBad?`${passed} of ${checks.length} checks passed`
    :o==="user_mapped_unverified"?"Column mapping reviewed"
    :o==="disambiguated"?"Column mapping certified"
    :nWarn?"Validation needs review"
    :nFixed?`${checks.length} of ${checks.length} checks pass after corrections`
    :"All checks passed";
  return{checks,nBad,nFixed,nWarn,passed,head,findings,witnesses,corrsC,td};
}
function renderCertificate(rep){
  const{checks,nBad,head,findings,witnesses,corrsC,td}=computeValidationChecks(rep,ACCEPTED);
  const totalCorrs=td?.totalCorrections||[];
  const SHOWN=6;
  const excRow=f=>{
    const col=(rep.table&&rep.table.columns[f.culprit_column]&&rep.table.columns[f.culprit_column].header)||"";
    const ci=corrsC.findIndex(x=>x.label===f.row_label&&x.implied===f.proposed_correction);
    const c=ci>=0?corrsC[ci]:null;
    const label=htmlEsc(f.row_label);
    const jobCell=c
      ?`<td class="corr-job-cell has-toggle"><div class="corr-job-line"><button class="evtoggle" data-ev="${ci}" aria-label="show the math">\u25B8</button><span class="corr-job-name" title="${label}">${label}</span></div><small>${htmlEsc(col)}</small></td>`
      :`<td class="corr-job-cell"><div class="corr-job-line"><span class="corr-job-name" title="${label}">${label}</span></div><small>${htmlEsc(col)}</small></td>`;
    const use=c?`<input type="checkbox" class="certbox" data-ci="${ci}" ${ACCEPTED.has(ci)?"":"checked"} aria-label="revert to the printed value for ${f.row_label}">`
               :`<span style="color:var(--muted);font-size:12px">review</span>`;
    const confirmation=!c?"":c.proof_kind==="joint"?"joint proof":
      c.proof_kind==="inherited"?"validated inputs":
      `${c.checks} way${c.checks===1?"":"s"}`;
    const ev=c?`<tr class="evrow hidden" data-ev="${ci}"><td colspan="5">
        <p class="evtitle">Formulas used · confirmed ${confirmation}${c.corroborated?" plus the totals row":""}</p>
        <div class="evgrid">
          ${(c.basis||[]).map(b=>`<div class="eq"><span class="eqname">${formulaHeading(b)}</span><div class="eqline"><span>${b}</span><span class="fr">\u2192 $${fmt$(c.implied)}</span></div></div>`).join("")}
          ${c.corroborated?`<div class="eq"><span class="eqname">Stated total</span><div class="eqline"><span>stated total \u2212 the other rows</span><span class="fr">\u2192 $${fmt$(c.implied)}</span></div></div>`:""}
        </div>
        <p class="evnote">${c.proof_kind==="joint"?"this is part of the unique smallest repair that makes every applicable formula pass":
          c.proof_kind==="inherited"?"the other values in this formula are independently pinned down; changing any of them breaks another formula":
          `every independent check lands on the same number; the printed $${fmt$(c.printed)} satisfies none of them`}</p>
      </td></tr>`:"";
    return `<tr>${jobCell}
      <td class="mny">${f.observed!=null?`<span class="bad">$${fmt$(f.observed)}</span>`:"\u2014"}</td>
      <td class="mny">${f.proposed_correction!=null?`$${fmt$(f.proposed_correction)}`:"\u2014"}</td>
      <td class="cause-cell">${causeText(f.classification,f.observed,f.proposed_correction,f.classification_label)}</td>
      <td style="text-align:right">${use}</td></tr>${ev}`;
  };
  const totalExcRow=t=>{
    const label=htmlEsc(t.header||variableName(t.variable)||"Stated total");
    const use=`<input type="checkbox" class="totalbox" data-total-key="${htmlEsc(t.correctionKey)}" ${ACCEPTED.has(t.correctionKey)?"":"checked"} aria-label="revert to the printed total for ${label}">`;
    return `<tr><td class="corr-job-cell"><div class="corr-job-line"><span class="corr-job-name">TOTAL</span></div><small>${label}</small></td>
      <td class="mny"><span class="bad">$${fmt$(t.stated)}</span></td>
      <td class="mny">$${fmt$(t.proposedCorrection)}</td>
      <td class="cause-cell">does not match job sum</td>
      <td style="text-align:right">${use}</td></tr>`;
  };
  const hasCorrections=findings.length||totalCorrs.length;
  const excHTML=hasCorrections?`<p style="font-size:12.5px;color:var(--muted);margin:14px auto 8px;max-width:62ch">Corrections are applied automatically. Check Revert to printed on any row to keep the document's value instead, then press Update figures.</p>
  <div class="cgroup" style="text-align:left">
    <table class="ctab"><thead><tr>
      <th>Job</th><th style="text-align:right">Flagged</th><th style="text-align:right">Correction</th><th style="text-align:right">Cause</th>
      <th style="text-align:right"><label style="display:inline-flex;gap:6px;align-items:center;cursor:pointer">Revert to printed <input type="checkbox" id="useAll"></label></th>
    </tr></thead><tbody>${findings.map(excRow).join("")}${totalCorrs.length&&findings.length?`<tr><td colspan="5" style="padding:12px 0 4px;color:var(--muted);font-size:10.5px;letter-spacing:.06em;text-transform:uppercase;border-bottom:0">Stated totals</td></tr>`:""}${totalCorrs.map(totalExcRow).join("")}</tbody></table>
  </div>
  ${(corrsC.length||totalCorrs.length)?`<div style="display:flex;align-items:center;justify-content:center;gap:12px;margin:12px 0 0">
    ${corrsC.length?'<button class="btn" id="reviewSource">Review source</button>':""}
    <button class="btn" id="applySel">Update figures</button>
    <span id="applyStatus" style="font-size:12.5px;color:var(--muted)"></span>
  </div>`:""}`:"";
  function corrStatus(rep,f){
    const c=((rep.analysis||{}).corrections||[]).find(x=>x.label===f.row_label&&x.implied===f.proposed_correction);
    if(!c)return f.proposed_correction!=null?". Needs manual review":"";
    return c.corroborated?". Confirmed "+(c.checks+1)+" ways including the totals row; applied by default and reversible below"
                         :". Applied by default; keep it or restore the printed value below";
  }
  const m=rep.metrics||{};
  const detail=witnesses.length?`
    <details class="more"><summary>Identities verified</summary><ul>
      ${witnesses.map(w=>`<li><span class="num">${w.business_form}</span> (${w.n_rows} rows, max residual ${(+w.max_abs_residual).toFixed(2)})</li>`).join("")}
      ${m.api_calls?`<li>${m.api_calls} model call(s)${
        modelCostBreakdown(m)?`: ${modelCostBreakdown(m)}`:""
      }; total $${(+m.cost_usd).toFixed(4)}</li>`:""}
    </ul></details>`:"";
  $("#certificate").innerHTML=`<div class="cert"><div class="cert-inner">
    <div class="kicker">validation results${SECTIONS.length>1?" \u00b7 "+secLabel(SECTIONS[ACTIVE]).name.toLowerCase()+" \u00b7 "+secLabel(SECTIONS[ACTIVE]).pg:""}</div>
    <h2 style="font-size:22px">${head}</h2>
    <details class="more checks-fold"><summary>${nBad?"Review":"Show"} the ${checks.length} checks</summary>
    <div style="margin-top:12px">${checks.map(c=>{
      const ic=c.st==="ok"||c.st==="fixed"?"\u2713":c.st==="warn"?"\u25B3":"\u2715";
      const col=c.st==="ok"||c.st==="fixed"?"var(--sage-deep)":c.st==="warn"?"var(--amber)":"var(--brick)";
      return `<div class="chk"><span class="ic" style="color:${col}">${ic}</span><div><p class="cl">${c.label}</p>${c.note?`<p class="cn">${c.note}</p>`:""}</div></div>`;
    }).join("")}</div></details>
    <div class="go" style="display:flex;justify-content:center;gap:10px;flex-wrap:wrap;margin-top:16px">
      ${BATCH_MODE?'<button class="btn" onclick="renderBatch()">← All validations</button>':""}
      <button class="btn primary" onclick="${BATCH_MODE?`renderBatchItemAnalysis(${BATCH_ACTIVE})`:"VIEW='dash';renderDash(REPORT);show('dash');window.scrollTo(0,0)"}">View underwriting analysis</button>
    </div>
    <section class="validation-corrections"><h3>Errors and corrections</h3>
      ${excHTML||'<p class="empty" style="text-align:left">No errors or corrections for this schedule.</p>'}
    </section>
    ${headerComparisonHTML(rep)}
    ${detail}
  </div></div>`;
  const ua=$("#useAll"),st=$("#applyStatus"),ap=$("#applySel"),src=$("#reviewSource");
  /* Boxes now mean "revert to printed": checked = keep the document's value.
     ACCEPTED remains the set of applied corrections, so it is the complement
     of the checked boxes. */
  const correctionKeys=()=>[...corrsC.map((_,i)=>i),...totalCorrs.map(t=>t.correctionKey)];
  const selection=()=>{
    const revertedRows=new Set([...document.querySelectorAll(".certbox:checked")].map(b=>+b.dataset.ci));
    const revertedTotals=new Set([...document.querySelectorAll(".totalbox:checked")].map(b=>b.dataset.totalKey));
    const selected=new Set(corrsC.map((_,i)=>i).filter(i=>!revertedRows.has(i)));
    totalCorrs.forEach(t=>{if(!revertedTotals.has(t.correctionKey))selected.add(t.correctionKey);});
    return selected;
  };
  const syncUA=()=>{if(ua){const boxes=[...document.querySelectorAll(".certbox,.totalbox")];
    ua.checked=boxes.length>0&&boxes.every(b=>b.checked);}};
  const syncStatus=()=>{if(!st)return;
    const sel=selection(),keys=correctionKeys();
    const active=keys.filter(k=>sel.has(k)).length;
    const same=keys.every(k=>sel.has(k)===ACCEPTED.has(k));
    st.textContent=same?`${active} of ${keys.length} correction${keys.length===1?"":"s"} applied`:"selection changed; update the figures when ready";
    st.style.color=same?"var(--muted)":"var(--brick)";};
  document.querySelectorAll(".certbox,.totalbox").forEach(b=>b.onchange=()=>{syncUA();syncStatus();});
  if(ua){syncUA();ua.onchange=()=>{document.querySelectorAll(".certbox,.totalbox").forEach(b=>{b.checked=ua.checked;});syncStatus();};}
  if(ap)ap.onclick=()=>{
    const applied=selection(),keys=correctionKeys();const restored=keys.filter(k=>!applied.has(k)).length;
    ACCEPTED=applied;
    renderCertificate(rep);
    const st2=$("#applyStatus");
    if(st2){st2.textContent=`✓ ${keys.length-restored} applied${restored?`; ${restored} kept as printed`:""}`;
      st2.style.color="var(--sage-deep)";}
  };
  if(src)src.onclick=()=>openSourceReview(rep);
  syncStatus();
  document.querySelectorAll(".evtoggle").forEach(t=>t.onclick=()=>{
    const r=document.querySelector(`.evrow[data-ev="${t.dataset.ev}"]`);
    if(r){r.classList.toggle("hidden");t.textContent=r.classList.contains("hidden")?"\u25B8":"\u25BE";}});
}

