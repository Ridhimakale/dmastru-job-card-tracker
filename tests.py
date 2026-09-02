"""Dependency-free integration checks for the Job Card Tracker."""
import http.client, json, threading, time, uuid, zipfile
from io import BytesIO
from server import API, setup
from http.server import ThreadingHTTPServer

PORT = 8012

def request(method, path, body=None, token=None):
    headers = {"Content-Type":"application/json"}
    if token: headers["Authorization"]="Bearer "+token
    c=http.client.HTTPConnection("127.0.0.1",PORT,timeout=5)
    c.request(method,path,json.dumps(body).encode() if body is not None else None,headers)
    r=c.getresponse(); raw=r.read(); c.close()
    try: data=json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError): data=raw
    return r.status,data

def login(email, password="demo"):
    status,user=request("POST","/api/login",{"email":email,"password":password})
    assert status==200,(status,user); return user

def main():
    setup(); httpd=ThreadingHTTPServer(("127.0.0.1",PORT),API); thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start(); time.sleep(.1)
    try:
        admin=login("admin@tracker.local"); management=login("management@tracker.local"); consultant=login("consultant@tracker.local")
        assert request("POST","/api/login",{"email":"admin@tracker.local","password":"wrong"})[0]==401
        assert request("GET","/api/job-cards")[0]==401
        assert request("GET","/api/users",token=management["token"])[0]==401
        status,users=request("GET","/api/users",token=admin["token"]); assert status==200 and any(u["id"]==consultant["id"] for u in users)
        number="TEST-"+uuid.uuid4().hex[:8]
        status,card=request("POST","/api/job-cards",{"number":number,"msme":"Integration Test MSME","start_date":"2026-08-01","end_date":"2026-10-01","required_md":2,"lead_consultant_id":consultant["id"]},admin["token"]); assert status==201,card
        card_id=card["id"]
        assert request("POST",f"/api/job-cards/{card_id}/visits",{},admin["token"])[0]==403
        status,detail=request("GET",f"/api/job-cards/{card_id}",token=consultant["token"]); assert status==200 and len(detail["milestones"])==3
        visit={"milestone_id":detail["milestones"][0]["id"],"visit_date":"2026-08-25","hours":6,"minutes":30,"work_details":"Validated visit","rating":"A"}
        status,result=request("POST",f"/api/job-cards/{card_id}/visits",visit,consultant["token"]); assert status==201 and result["visit_number"]==1
        status,detail=request("GET",f"/api/job-cards/{card_id}",token=consultant["token"]); assert status==200 and detail["completed_hours"]==6.5 and detail["completed_md"]==0.81
        upload={"type":"Case Study","file_name":"evidence.pdf","mime_type":"application/pdf","content_base64":"dGVzdCBwZGYgY29udGVudA=="}
        status,doc=request("POST",f"/api/job-cards/{card_id}/documents",upload,consultant["token"]); assert status==201,doc
        status,file=request("GET",f"/api/documents/{doc['id']}/download",token=consultant["token"]); assert status==200 and file==b"test pdf content"
        for stage in ("CONSULTANT","UNIT_REPRESENTATIVE","DIRECTOR"):
            assert request("POST",f"/api/job-cards/{card_id}/approvals",{"stage":stage,"decision":"APPROVED","comment":"checked"},management["token"])[0]==201
        status,detail=request("GET",f"/api/job-cards/{card_id}",token=management["token"]); assert status==200 and detail["status"]=="COMPLETED"
        status,csv=request("GET","/api/reports/projects.csv",token=management["token"]); assert status==200 and number.encode() in csv
        status,xlsx=request("GET","/api/reports/projects.xlsx",token=management["token"]); assert status==200 and zipfile.is_zipfile(BytesIO(xlsx))
        status,notes=request("GET","/api/notifications",token=consultant["token"]); assert status==200 and notes
        print("PASS: authentication, RBAC, job cards, visits, documents, approvals, notifications, and exports")
    finally: httpd.shutdown(); httpd.server_close()

if __name__=="__main__": main()
