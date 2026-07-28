from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


server_path = Path("server.py")
server = server_path.read_text()
server = replace_once(
    server,
    '''        if n:\n            kind = state.get("media_type", "")\n            unit = "strip" if str(kind).startswith("image/") else "page"\n            return [f"Document split into {_plural(n, unit)}"]\n''',
    '''        if n:\n            return ["Reading schedule"]\n''',
    "hide page splitting narration",
)
server_path.write_text(server)

index_path = Path("static/index.html")
index = index_path.read_text()
index = replace_once(
    index,
    '''function runDuration(seconds){\n  if(!(seconds>0))return"";\n  if(seconds<60)return`${seconds.toFixed(1)}s`;\n  const m=Math.floor(seconds/60);\n  return`${m}m ${Math.round(seconds-60*m)}s`;\n}\n''',
    '''function runDuration(seconds){\n  if(!(seconds>0))return"";\n  if(seconds<60)return`${seconds.toFixed(1)}s`;\n  const m=Math.floor(seconds/60);\n  return`${m}m ${Math.round(seconds-60*m)}s`;\n}\n/* CSV runs do not make a model call, so the backend has no model elapsed time\n   to report. Fill only that blank with the browser wall clock; model-backed\n   reports keep their existing backend duration. Apply it to every section so\n   the same footer works for single documents and batch review. */\nfunction attachClientElapsed(doc,seconds){\n  if(!doc||!(seconds>0))return doc;\n  const apply=rep=>{\n    if(!rep||typeof rep!=="object")return;\n    rep.metrics=rep.metrics||{};\n    if(!(+(rep.metrics.elapsed_seconds)>0))rep.metrics.elapsed_seconds=seconds;\n  };\n  apply(doc);\n  (doc.tables||[]).forEach(t=>(t.sections||[]).forEach(s=>apply(s.report)));\n  return doc;\n}\n''',
    "add client elapsed fallback",
)
index = replace_once(
    index,
    '''async function stream(url,opts){\n  show("processing");$("#batchProgress").classList.add("hidden");hideBatchLanes();\n  $("#log").innerHTML="";\n  RUNNING=true;DOTS=0;LATEST_PROGRESS=null;\n  try{\n    const doc=await readStream(url,opts);\n    RUNNING=false;finishProgressLine();\n''',
    '''async function stream(url,opts){\n  show("processing");$("#batchProgress").classList.add("hidden");hideBatchLanes();\n  $("#log").innerHTML="";\n  RUNNING=true;DOTS=0;LATEST_PROGRESS=null;\n  const startedAt=performance.now();\n  try{\n    const doc=await readStream(url,opts);\n    attachClientElapsed(doc,(performance.now()-startedAt)/1000);\n    RUNNING=false;finishProgressLine();\n''',
    "time single uploads",
)
index = replace_once(
    index,
    '''      const i=next++,item=BATCH_ITEMS[i];\n      item.status="processing";item.line="Reading and validating schedule";\n      running++;updateBatchLane(i);tick();\n      const fd=new FormData();fd.append("file",item.file);\n''',
    '''      const i=next++,item=BATCH_ITEMS[i];\n      item.status="processing";item.line="Reading and validating schedule";\n      running++;updateBatchLane(i);tick();\n      const itemStartedAt=performance.now();\n      const fd=new FormData();fd.append("file",item.file);\n''',
    "start batch item timer",
)
index = replace_once(
    index,
    '''        item.doc=await readStream(API+"/api/scan",{method:"POST",body:fd},\n          message=>{item.line=message;updateBatchLane(i);});\n        item.error=batchDocError(item.doc);\n''',
    '''        item.doc=await readStream(API+"/api/scan",{method:"POST",body:fd},\n          message=>{item.line=message;updateBatchLane(i);});\n        attachClientElapsed(item.doc,(performance.now()-itemStartedAt)/1000);\n        item.error=batchDocError(item.doc);\n''',
    "time batch uploads",
)
index_path.write_text(index)
