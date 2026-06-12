import io, json, sys
sys.path.insert(0, ".")
import openpyxl
from fastapi.testclient import TestClient


def make_xlsx():
    sys.path.insert(0, ".")
    from wipple.demo import demo_raw_table_12
    rt = demo_raw_table_12()
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "WIP"
    ws.append(["Anytown Builders, Inc."]); ws.append([])
    ws.append(rt["headers"])
    for r in rt["rows"]:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def test_xlsx_upload_end_to_end():
    import server
    c = TestClient(server.app)
    files = {"file": ("book.xlsx", make_xlsx(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    with c.stream("POST", "/api/scan", files=files) as r:
        ev = [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]
    rep = ev[-1]
    assert rep["overall_status"] == "verified_mapping_with_findings"
    assert rep["metrics"]["api_calls"] == 0          # fully deterministic
    assert rep["findings"][0]["row_label"] == "Harbor District Garage"


def test_sniffing():
    from wipple.ingest import sniff
    assert sniff(b"%PDF-1.7 etc") == "application/pdf"
    assert sniff(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert sniff(b"\xff\xd8\xff\xe0..") == "image/jpeg"
    assert sniff(b"a,b,c\n1,2,3", "wip.csv") == "csv"
