"""Job Card tracker – small, dependency-free local web application."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
import zipfile
from datetime import date, datetime
from io import BytesIO
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = ROOT / "tracker.db"
UPLOADS = ROOT / "uploads"


def connection():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def setup():
    db = connection()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT);
    CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS job_cards (
      id INTEGER PRIMARY KEY, number TEXT UNIQUE NOT NULL, msme TEXT NOT NULL, zed_id TEXT,
      certificate_level TEXT, location TEXT, lead_consultant_id INTEGER, start_date TEXT NOT NULL,
      end_date TEXT NOT NULL, required_md REAL NOT NULL CHECK(required_md > 0), status TEXT NOT NULL DEFAULT 'DRAFT',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(lead_consultant_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS milestones (id INTEGER PRIMARY KEY, job_card_id INTEGER NOT NULL, code TEXT NOT NULL,
      name TEXT NOT NULL, planned_visits INTEGER NOT NULL CHECK(planned_visits >= 0), closing_date TEXT, status TEXT NOT NULL DEFAULT 'PENDING',
      UNIQUE(job_card_id, code), FOREIGN KEY(job_card_id) REFERENCES job_cards(id) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS visits (id INTEGER PRIMARY KEY, job_card_id INTEGER NOT NULL, milestone_id INTEGER NOT NULL,
      visit_number INTEGER NOT NULL, visit_date TEXT NOT NULL, hours INTEGER NOT NULL CHECK(hours >= 0),
      minutes INTEGER NOT NULL CHECK(minutes BETWEEN 0 AND 59), work_details TEXT NOT NULL, rating TEXT NOT NULL CHECK(rating IN ('A','B','C','D','E')),
      status TEXT NOT NULL DEFAULT 'SUBMITTED', created_by INTEGER, UNIQUE(job_card_id, visit_number),
      FOREIGN KEY(job_card_id) REFERENCES job_cards(id) ON DELETE CASCADE, FOREIGN KEY(milestone_id) REFERENCES milestones(id), FOREIGN KEY(created_by) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, job_card_id INTEGER NOT NULL, type TEXT NOT NULL,
      file_name TEXT NOT NULL, storage_path TEXT, mime_type TEXT, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'PENDING REVIEW', uploaded_by INTEGER, uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(job_card_id) REFERENCES job_cards(id) ON DELETE CASCADE, FOREIGN KEY(uploaded_by) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY, job_card_id INTEGER NOT NULL, stage TEXT NOT NULL,
      decision TEXT NOT NULL, comment TEXT, decided_by INTEGER, decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(job_card_id) REFERENCES job_cards(id) ON DELETE CASCADE, FOREIGN KEY(decided_by) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL, entity TEXT NOT NULL,
      entity_id INTEGER, details TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, message TEXT NOT NULL, read_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(user_id) REFERENCES users(id));
    """)
    # Allows the original demo database to be upgraded without being deleted.
    columns = {r[1] for r in db.execute("PRAGMA table_info(users)")}
    if "password_hash" not in columns: db.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    document_columns = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
    for name, definition in (("storage_path","TEXT"),("mime_type","TEXT"),("version","INTEGER NOT NULL DEFAULT 1")):
        if name not in document_columns: db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        db.executemany("INSERT INTO users(name,role,email) VALUES (?,?,?)", [
          ("Aarav Admin", "ADMIN", "admin@tracker.local"), ("Meera Management", "MANAGEMENT", "management@tracker.local"),
          ("Rohit Consultant", "END_USER", "consultant@tracker.local")])
    db.execute("UPDATE users SET password_hash=? WHERE password_hash IS NULL", (password_hash("demo"),))
    db.commit(); db.close()


def rows(db, query, args=()): return [dict(r) for r in db.execute(query, args).fetchall()]
def log(db, actor, action, entity, entity_id=None, details=""):
    db.execute("INSERT INTO audit_logs(actor,action,entity,entity_id,details) VALUES (?,?,?,?,?)", (actor, action, entity, entity_id, details))


def password_hash(password):
    salt = secrets.token_bytes(16)
    return salt.hex()+":"+hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def password_matches(password, stored):
    try:
        salt, digest = stored.split(":", 1)
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, AttributeError): return False


def notify(db, user_id, message):
    if user_id: db.execute("INSERT INTO notifications(user_id,message) VALUES (?,?)", (user_id, message))


class API(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT / "web"), **kwargs)
    def json(self, status, value):
        data = json.dumps(value, default=str).encode(); self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        try: return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError: raise ValueError("Invalid JSON body")
    def current_user(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "): return None
        db=connection(); user=db.execute("""SELECT u.id,u.name,u.role,u.email FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token=? AND s.expires_at>datetime('now')""",(header[7:],)).fetchone(); db.close()
        return dict(user) if user else None
    def require(self, *roles):
        user=self.current_user()
        if not user: raise PermissionError("Authentication required.")
        if roles and user["role"] not in roles: raise PermissionError("You do not have permission for this action.")
        return user
    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"): return super().do_GET()
        try:
            user=self.require()
            if path == "/api/users":
                if user["role"] != "ADMIN": raise PermissionError("Only Admin can view users.")
                return self.json(200, self.users())
            if path == "/api/notifications":
                db=connection(); data=rows(db,"SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",(user["id"],)); db.close(); return self.json(200,data)
            if path == "/api/job-cards": return self.json(200, self.cards(user))
            if path.startswith("/api/documents/") and path.endswith("/download"):
                return self.download_document(int(path.split('/')[3]), user)
            if path.startswith("/api/job-cards/"):
                try: return self.json(200, self.card(int(path.rsplit('/', 1)[1]), user))
                except LookupError: return self.json(404, {"error":"Job card not found"})
            if path == "/api/dashboard": return self.json(200, self.dashboard(user))
            if path == "/api/audit-logs":
                if user["role"] not in ("ADMIN","MANAGEMENT"): raise PermissionError("Audit logs are restricted.")
                db=connection(); data=rows(db,"SELECT * FROM audit_logs ORDER BY id DESC LIMIT 50"); db.close(); return self.json(200,data)
            if path == "/api/reports/projects.csv": return self.export_csv(user)
            if path == "/api/reports/projects.xlsx": return self.export_xlsx(user)
            return self.json(404,{"error":"Unknown endpoint"})
        except PermissionError as e: return self.json(401,{"error":str(e)})
    def do_POST(self):
        try:
            path=urlparse(self.path).path; data=self.body()
            if path == "/api/login": return self.login(data)
            user=self.require()
            if path == "/api/users": return self.create_user(data, user)
            if path == "/api/job-cards": return self.create_card(data, user)
            if path.endswith("/visits"): return self.create_visit(int(path.split('/')[3]), data, user)
            if path.endswith("/documents"): return self.create_document(int(path.split('/')[3]), data, user)
            if path.startswith("/api/documents/") and path.endswith("/review"): return self.review_document(int(path.split('/')[3]), data, user)
            if path.endswith("/approvals"): return self.create_approval(int(path.split('/')[3]), data, user)
            return self.json(404,{"error":"Unknown endpoint"})
        except PermissionError as e: return self.json(403,{"error":str(e)})
        except (ValueError, KeyError) as e: return self.json(400,{"error":str(e)})
        except sqlite3.IntegrityError as e: return self.json(400,{"error":str(e)})
    def users(self):
        db=connection(); data=rows(db,"SELECT id,name,role,email FROM users ORDER BY role,name"); db.close(); return data
    def cards(self, user=None):
        db=connection(); where="WHERE j.lead_consultant_id=?" if user and user["role"]=="END_USER" else ""
        args=(user["id"],) if where else ()
        data=rows(db,f"""SELECT j.*, u.name consultant, COALESCE(SUM(v.hours+v.minutes/60.0),0) completed_hours
          FROM job_cards j LEFT JOIN users u ON u.id=j.lead_consultant_id LEFT JOIN visits v ON v.job_card_id=j.id {where} GROUP BY j.id ORDER BY j.id DESC""",args); db.close()
        for c in data: c.update(metrics(c["required_md"], c["completed_hours"]))
        return data
    def card(self, card_id, user=None):
        cards=[c for c in self.cards(user) if c["id"]==card_id]
        if not cards: raise LookupError()
        db=connection(); card=cards[0]
        card["milestones"]=rows(db,"""SELECT m.*,COUNT(v.id) actual_visits FROM milestones m LEFT JOIN visits v ON v.milestone_id=m.id WHERE m.job_card_id=? GROUP BY m.id ORDER BY m.code""",(card_id,))
        for m in card["milestones"]: m["progress"] = round(100*m["actual_visits"]/m["planned_visits"],2) if m["planned_visits"] else 0
        card["visits"]=rows(db,"SELECT v.*,m.code milestone FROM visits v JOIN milestones m ON m.id=v.milestone_id WHERE v.job_card_id=? ORDER BY visit_number DESC",(card_id,))
        card["documents"]=rows(db,"SELECT * FROM documents WHERE job_card_id=? ORDER BY id DESC",(card_id,))
        card["approvals"]=rows(db,"SELECT a.*,u.name approver FROM approvals a LEFT JOIN users u ON u.id=a.decided_by WHERE job_card_id=? ORDER BY id",(card_id,))
        db.close(); return card
    def login(self,d):
        db=connection(); u=db.execute("SELECT id,name,role,email,password_hash FROM users WHERE email=?",(d.get("email",""),)).fetchone()
        if not u or not password_matches(d.get("password",""),u["password_hash"]): db.close(); return self.json(401,{"error":"Invalid email or password."})
        token=secrets.token_urlsafe(32); expiry=datetime.utcnow().replace(microsecond=0).isoformat()
        db.execute("INSERT INTO sessions(token,user_id,expires_at) VALUES (?,?,datetime('now','+8 hours'))",(token,u["id"])); log(db,u["name"],"logged in","session",u["id"]); db.commit(); db.close()
        return self.json(200,{"id":u["id"],"name":u["name"],"role":u["role"],"email":u["email"],"token":token})
    def create_user(self,d,actor):
        if actor["role"]!="ADMIN": raise PermissionError("Only Admin can create users.")
        if d.get("role") not in ("ADMIN","MANAGEMENT","END_USER") or not d.get("name") or not d.get("email") or len(d.get("password",""))<8: raise ValueError("Name, valid role, email, and an 8-character password are required.")
        db=connection(); cur=db.execute("INSERT INTO users(name,role,email,password_hash) VALUES (?,?,?,?)",(d["name"],d["role"],d["email"],password_hash(d["password"]))); log(db,actor["name"],"created","user",cur.lastrowid,d["email"]); db.commit(); db.close(); return self.json(201,{"id":cur.lastrowid})
    def create_card(self,d,actor):
        if actor["role"]!="ADMIN": raise PermissionError("Only Admin can create job cards.")
        required=["number","msme","start_date","end_date","required_md","lead_consultant_id"]
        if any(not d.get(x) for x in required): raise ValueError("Complete all required job card fields.")
        db=connection(); cur=db.execute("""INSERT INTO job_cards(number,msme,zed_id,certificate_level,location,lead_consultant_id,start_date,end_date,required_md,status)
          VALUES (?,?,?,?,?,?,?,?,?,'ASSIGNED')""",tuple(d.get(x) for x in ["number","msme","zed_id","certificate_level","location","lead_consultant_id","start_date","end_date","required_md"]))
        for m in d.get("milestones", [{"code":"M1","name":"Assessment","planned_visits":2},{"code":"M2","name":"Implementation","planned_visits":6},{"code":"M3","name":"Closure","planned_visits":6}]): db.execute("INSERT INTO milestones(job_card_id,code,name,planned_visits,closing_date) VALUES (?,?,?,?,?)",(cur.lastrowid,m["code"],m["name"],m["planned_visits"],m.get("closing_date")))
        notify(db,d["lead_consultant_id"],f"You were assigned job card {d['number']}."); log(db,actor["name"],"created","job_card",cur.lastrowid,d["number"]); db.commit(); db.close(); return self.json(201,{"id":cur.lastrowid})
    def create_visit(self,card_id,d,actor):
        if actor["role"]!="END_USER": raise PermissionError("Only assigned consultants can submit visits.")
        for x in ("milestone_id","visit_date","hours","minutes","work_details","rating"):
            if d.get(x) is None or d.get(x)=="": raise ValueError(f"{x.replace('_',' ')} is required.")
        h,m=int(d["hours"]),int(d["minutes"])
        if h<0 or not 0<=m<60: raise ValueError("Hours must be non-negative and minutes must be 0–59.")
        db=connection(); exists=db.execute("SELECT 1 FROM job_cards WHERE id=?",(card_id,)).fetchone()
        if not exists or not db.execute("SELECT 1 FROM job_cards WHERE id=? AND lead_consultant_id=?",(card_id,actor["id"])).fetchone(): db.close(); raise PermissionError("You are not assigned to this job card.")
        milestone=db.execute("SELECT 1 FROM milestones WHERE id=? AND job_card_id=?",(d["milestone_id"],card_id)).fetchone()
        if not milestone: db.close(); raise ValueError("Milestone does not belong to this job card.")
        num=db.execute("SELECT COALESCE(MAX(visit_number),0)+1 FROM visits WHERE job_card_id=?",(card_id,)).fetchone()[0]
        cur=db.execute("INSERT INTO visits(job_card_id,milestone_id,visit_number,visit_date,hours,minutes,work_details,rating,created_by) VALUES (?,?,?,?,?,?,?,?,?)",(card_id,d["milestone_id"],num,d["visit_date"],h,m,d["work_details"],d["rating"],actor["id"]))
        db.execute("UPDATE job_cards SET status='IN PROGRESS' WHERE id=? AND status='ASSIGNED'",(card_id,)); notify(db,1,f"Visit {num} was submitted for job card {card_id}."); log(db,actor["name"],"submitted","visit",cur.lastrowid,f"Visit {num}"); db.commit(); db.close(); return self.json(201,{"id":cur.lastrowid,"visit_number":num})
    def create_document(self,card_id,d,actor):
        if actor["role"]!="END_USER": raise PermissionError("Only assigned consultants can submit documents.")
        if not d.get("type") or not d.get("file_name"): raise ValueError("Document type and filename are required.")
        db=connection()
        if not db.execute("SELECT 1 FROM job_cards WHERE id=? AND lead_consultant_id=?",(card_id,actor["id"])).fetchone(): db.close(); raise PermissionError("You are not assigned to this job card.")
        allowed={".pdf",".png",".jpg",".jpeg",".docx",".xlsx"}; suffix=Path(d["file_name"]).suffix.lower(); raw=base64.b64decode(d.get("content_base64","") or "")
        if suffix not in allowed or not raw or len(raw)>10*1024*1024: db.close(); raise ValueError("Upload a PDF, image, DOCX, or XLSX file up to 10 MB.")
        UPLOADS.mkdir(exist_ok=True); safe=uuid.uuid4().hex+suffix; (UPLOADS/safe).write_bytes(raw)
        cur=db.execute("INSERT INTO documents(job_card_id,type,file_name,storage_path,mime_type,uploaded_by) VALUES (?,?,?,?,?,?)",(card_id,d["type"],Path(d["file_name"]).name,safe,d.get("mime_type","application/octet-stream"),actor["id"])); notify(db,1,f"{d['type']} is awaiting review for job card {card_id}."); log(db,actor["name"],"submitted","document",cur.lastrowid,d["type"]); db.commit(); db.close(); return self.json(201,{"id":cur.lastrowid})
    def create_approval(self,card_id,d,actor):
        if actor["role"] not in ("MANAGEMENT",): raise PermissionError("Only Management can record approvals.")
        stages=["CONSULTANT","UNIT_REPRESENTATIVE","DIRECTOR"]; stage=d.get("stage"); decision=d.get("decision")
        if stage not in stages or decision not in ("APPROVED","REJECTED"): raise ValueError("Invalid approval.")
        db=connection(); prior=stages.index(stage)
        if prior and not db.execute("SELECT 1 FROM approvals WHERE job_card_id=? AND stage=? AND decision='APPROVED'",(card_id,stages[prior-1])).fetchone(): db.close(); raise ValueError("The previous approval stage must be approved first.")
        cur=db.execute("INSERT INTO approvals(job_card_id,stage,decision,comment,decided_by) VALUES (?,?,?,?,?)",(card_id,stage,decision,d.get("comment",""),actor["id"])); 
        if stage=="DIRECTOR" and decision=="APPROVED": db.execute("UPDATE job_cards SET status='COMPLETED' WHERE id=?",(card_id,))
        if decision=="REJECTED": db.execute("UPDATE job_cards SET status='REJECTED' WHERE id=?",(card_id,))
        card=db.execute("SELECT lead_consultant_id,number FROM job_cards WHERE id=?",(card_id,)).fetchone(); notify(db,card["lead_consultant_id"],f"{stage.replace('_',' ')} {decision.lower()} job card {card['number']}."); log(db,actor["name"],decision.lower(),"approval",cur.lastrowid,stage); db.commit(); db.close(); return self.json(201,{"id":cur.lastrowid})
    def dashboard(self,user):
        cards=self.cards(user); today=date.today().isoformat()
        delayed=[c for c in cards if c["end_date"]<today and c["status"]!="COMPLETED"]
        return {"cards":cards,"kpis":{"total_projects":len(cards),"active_projects":sum(c["status"] in ("ASSIGNED","IN PROGRESS") for c in cards),"completed_projects":sum(c["status"]=="COMPLETED" for c in cards),"delayed_projects":len(delayed),"required_md":round(sum(c["required_md"] for c in cards),2),"completed_md":round(sum(c["completed_md"] for c in cards),2),"remaining_md":round(sum(c["remaining_md"] for c in cards),2)},"attention":delayed}
    def download_document(self, document_id, user):
        db=connection(); doc=db.execute("""SELECT d.* FROM documents d JOIN job_cards j ON j.id=d.job_card_id
            WHERE d.id=? AND (? != 'END_USER' OR j.lead_consultant_id=?)""",(document_id,user["role"],user["id"])).fetchone(); db.close()
        if not doc or not doc["storage_path"] or not (UPLOADS/doc["storage_path"]).is_file(): return self.json(404,{"error":"Document not found."})
        data=(UPLOADS/doc["storage_path"]).read_bytes(); self.send_response(200); self.send_header("Content-Type",doc["mime_type"] or "application/octet-stream"); self.send_header("Content-Disposition",f'attachment; filename="{doc["file_name"]}"'); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def review_document(self, document_id, data, user):
        if user["role"] not in ("ADMIN","MANAGEMENT"): raise PermissionError("Only Admin or Management can review documents.")
        decision=data.get("decision")
        comment=(data.get("comment") or "").strip()
        if decision not in ("APPROVED","REJECTED"): raise ValueError("Invalid document decision.")
        if decision=="REJECTED" and len(comment)<3: raise ValueError("A rejection comment is required.")
        db=connection()
        doc=db.execute("SELECT * FROM documents WHERE id=?",(document_id,)).fetchone()
        if not doc: db.close(); return self.json(404,{"error":"Document not found."})
        db.execute("UPDATE documents SET status=? WHERE id=?",(decision,document_id))
        owner=db.execute("SELECT uploaded_by,type FROM documents WHERE id=?",(document_id,)).fetchone()
        if owner: notify(db,owner["uploaded_by"],f"{owner['type']} was {decision.lower()}. {comment}")
        log(db,user["name"],decision.lower(),"document",document_id,comment)
        db.commit(); db.close()
        return self.json(200,{"id":document_id,"status":decision,"remarks":comment})
    def export_csv(self,user):
        if user["role"] not in ("ADMIN","MANAGEMENT"): raise PermissionError("Reports are restricted.")
        data=self.cards(user); output="Job Card,MSME,Consultant,Required MD,Completed MD,Status\n"+"\n".join(",".join('"'+str(c[x] or '').replace('"','""')+'"' for x in ("number","msme","consultant","required_md","completed_md","status")) for c in data)
        raw=output.encode(); self.send_response(200); self.send_header("Content-Type","text/csv"); self.send_header("Content-Disposition","attachment; filename=job-card-report.csv"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def export_xlsx(self,user):
        if user["role"] not in ("ADMIN","MANAGEMENT"): raise PermissionError("Reports are restricted.")
        data=self.cards(user); headers=["Job Card","MSME","Consultant","Required MD","Completed MD","Status"]
        def xmlrow(items): return "<row>"+"".join(f'<c t="inlineStr"><is><t>{str(x).replace("&","&amp;").replace("<","&lt;")}</t></is></c>' for x in items)+"</row>"
        sheet='<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+xmlrow(headers)+''.join(xmlrow([c[x] for x in ("number","msme","consultant","required_md","completed_md","status")]) for c in data)+"</sheetData></worksheet>"
        book=BytesIO()
        with zipfile.ZipFile(book,"w") as z:
            z.writestr("[Content_Types].xml",'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            z.writestr("_rels/.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr("xl/workbook.xml",'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Projects" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml",sheet)
        raw=book.getvalue(); self.send_response(200); self.send_header("Content-Type","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"); self.send_header("Content-Disposition","attachment; filename=job-card-report.xlsx"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)


def metrics(required_md, completed_hours):
    required_hours=required_md*8; complete=round(completed_hours/8,2)
    return {"required_hours":required_hours,"completed_hours":round(completed_hours,2),"completed_md":complete,"remaining_hours":round(required_hours-completed_hours,2),"remaining_md":round(required_md-complete,2),"overrun":completed_hours>required_hours}

if __name__ == "__main__":
    setup(); print("Job Card Tracker: http://127.0.0.1:8000"); ThreadingHTTPServer(("127.0.0.1",8000),API).serve_forever()
