const APP_STATE={
  document:{
    source:null,
    sections:[],
    correctionsBySection:[],
    activeSection:0,
    view:"certificate",
    get report(){return this.sections[this.activeSection]?.rep||null;},
    get accepted(){
      if(!this.correctionsBySection[this.activeSection])
        this.correctionsBySection[this.activeSection]=new Set();
      return this.correctionsBySection[this.activeSection];
    }
  },
  batch:{
    activeItem:-1,
    matchState:null,
    analysisMode:false,
    analysisScope:"portfolio"
  },
  progress:{running:false},
  billingTrajectory:{showAll:false}
};

function initializeDocumentState({source,sections,correctionsBySection,activeSection=0,view="certificate"}){
  const session=APP_STATE.document;
  session.source=source;
  session.sections=sections;
  session.correctionsBySection=correctionsBySection;
  session.activeSection=Math.max(0,Math.min(activeSection,Math.max(0,sections.length-1)));
  session.view=view;
  void session.accepted;
  return session;
}

function activateDocumentSection(index,{saveCurrent=true,view="certificate"}={}){
  const session=APP_STATE.document;
  if(!session.sections[index])return false;
  if(saveCurrent&&session.correctionsBySection[session.activeSection])
    session.correctionsBySection[session.activeSection]=new Set(session.accepted);
  session.activeSection=index;
  session.view=view;
  void session.accepted;
  return true;
}

function activateAnalysisDocument(report){
  return initializeDocumentState({
    source:report,
    sections:[{type:"wip",pages:[],rep:report}],
    correctionsBySection:[new Set()],
    activeSection:0,
    view:"dash"
  });
}

function replaceAcceptedCorrections(corrections){
  if(!(corrections instanceof Set))throw new TypeError("Accepted corrections must be a Set");
  const session=APP_STATE.document;
  session.correctionsBySection[session.activeSection]=corrections;
  return corrections;
}

function activeCorrectionStateIsAliased(){
  const session=APP_STATE.document;
  return session.accepted===session.correctionsBySection[session.activeSection];
}
