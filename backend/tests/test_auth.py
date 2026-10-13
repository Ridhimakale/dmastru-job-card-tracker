import os
from datetime import datetime, timedelta, timezone
from uuid import UUID
from io import BytesIO
from pathlib import Path
from zipfile import is_zipfile
Path("test_phase1.db").unlink(missing_ok=True)
os.environ.update(APP_ENV="test",DATABASE_URL="sqlite:///./test_phase1.db",JWT_SECRET="test-only-secret-value-with-32-bytes",RATE_LIMIT_MAX_REQUESTS="10000",LOGIN_RATE_LIMIT_MAX_REQUESTS="10000")
from fastapi.testclient import TestClient
from app import main
from app.database import SessionLocal
from app.models import JobCard, WorkflowAction
app=main.app

with TestClient(app) as client:
    def test_login_rbac_and_logout():
        bad=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'nope'}); assert bad.status_code==401
        login=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}); assert login.status_code==200
        token=login.json()['access_token']; headers={'Authorization':'Bearer '+token}
        assert client.get('/api/auth/me',headers=headers).status_code==200
        created=client.post('/api/users',headers=headers,json={'full_name':'Pending Consultant','email':'pending@example.com','password':'Strong@123','role':'END_USER'}); assert created.status_code==201 and created.json()['status']=='PENDING_ACTIVATION'
        assert client.post('/api/auth/logout',headers=headers).status_code==204
        assert client.get('/api/auth/me',headers=headers).status_code==401

    def test_release_security_headers_cors_and_rate_limiting():
        cors=client.options('/api/auth/login',headers={'Origin':'http://127.0.0.1:5173','Access-Control-Request-Method':'POST'})
        assert cors.status_code==200 and cors.headers['access-control-allow-origin']=='http://127.0.0.1:5173'
        health=client.get('/health'); assert health.headers['x-content-type-options']=='nosniff' and health.headers['x-frame-options']=='DENY'
        previous=main.RATE_MAX_REQUESTS
        try:
            main._rates.clear(); main.RATE_MAX_REQUESTS=1
            assert client.get('/health').status_code==200
            limited=client.get('/health'); assert limited.status_code==429 and 'retry-after' in limited.headers
        finally:
            main.RATE_MAX_REQUESTS=previous; main._rates.clear()

    def test_msme_job_card_and_assignment_access():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()
        unit=client.post('/api/auth/login',json={'email':'unit.rep@dmastru.local','password':'UnitRep@123'}).json()
        director=client.post('/api/auth/login',json={'email':'director@dmastru.local','password':'Director@123'}).json()
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant['access_token']}
        msme=client.post('/api/msmes',headers=ah,json={'name':'Acme Manufacturing','msme_code':'MSME-ACME','zed_id':'ZED-100','certificate_level':'Gold','contact_person':'Asha Rao','email':'asha@acme.example','phone':'9988776655','industry':'Manufacturing','unit_name':'Pune Unit'}); assert msme.status_code==201 and msme.json()['industry']=='Manufacturing' and msme.json()['email']=='asha@acme.example'
        invalid=client.post('/api/job-cards',headers=ah,json={'number':'DMP/001','msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-09-01','project_end_date':'2026-08-31','required_md':'14','lead_consultant_id':3}); assert invalid.status_code==422
        manual_number=client.post('/api/job-cards',headers=ah,json={'number':'MANUAL-001','msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-08-01','project_end_date':'2026-10-01','required_md':'14','lead_consultant_id':consultant['user']['id'],'unit_representative_id':unit['user']['id'],'director_id':director['user']['id']}); assert manual_number.status_code==422
        card=client.post('/api/job-cards',headers=ah,json={'msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-08-01','project_end_date':'2026-10-01','required_md':'14','lead_consultant_id':consultant['user']['id'],'unit_representative_id':unit['user']['id'],'director_id':director['user']['id']}); assert card.status_code==201 and card.json()['required_hours']=='112.00' and card.json()['number'].startswith('DMJC-2026-')
        integrity_db=SessionLocal();integrity_card=integrity_db.get(JobCard,card.json()['id']);integrity_card.number='DMJC-2026-99999'
        try: integrity_db.commit(); raise AssertionError('Generated Job Card number changed.')
        except ValueError: integrity_db.rollback()
        finally: integrity_db.close()
        assert client.patch(f"/api/job-cards/{card.json()['id']}",headers=ah,json={'number':'MANUAL-002'}).status_code==422
        reassigned=client.post(f"/api/job-cards/{card.json()['id']}/reassign",headers=ah,json={'assignment_type':'LEAD_CONSULTANT','user_id':consultant['user']['id'],'reason':'Confirm the resource allocation dates.','allocation_start_date':'2026-08-01','allocation_end_date':'2026-09-30'});assert reassigned.status_code==200
        history=client.get(f"/api/job-cards/{card.json()['id']}/assignments",headers=ah).json();lead_history=[a for a in history if a['assignment_type']=='LEAD_CONSULTANT'];assert len(lead_history)==2 and any(a['active'] and a['allocation_end_date']=='2026-09-30' for a in lead_history) and any(not a['active'] for a in lead_history)
        assert client.get('/api/job-cards',headers=ch).json()[0]['number'].startswith('DMJC-2026-')
        workspace=client.get(f"/api/job-cards/{card.json()['id']}/workspace",headers=ch);assert workspace.status_code==200 and workspace.json()['job_card']['id']==card.json()['id'] and 'milestones' in workspace.json() and 'related_tickets' in workspace.json()
        assert client.get('/api/msmes',headers=ch).status_code==403
        admin_dashboard=client.get('/api/dashboard/admin',headers=ah);assert admin_dashboard.status_code==200 and {'total_msmes','total_job_cards','job_cards_awaiting_consultant_assignment','job_cards_awaiting_payment','work_in_progress','returned_documents','milestones_awaiting_action','upcoming_visits','overdue_activities'}<=admin_dashboard.json()['kpis'].keys() and 'consultant_workload' in admin_dashboard.json() and 'upcoming_visits' in admin_dashboard.json()

    def test_milestones_visit_drafts_and_manday_engine():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant}
        card_id=client.get('/api/job-cards',headers=ah).json()[0]['id']
        lead_id=next(a['user_id'] for a in client.get(f'/api/job-cards/{card_id}/assignments',headers=ah).json() if a['assignment_type']=='LEAD_CONSULTANT')
        milestone=client.post(f'/api/job-cards/{card_id}/milestones',headers=ah,json={'code':'M1','name':'Assessment','planned_visits':2,'planned_mandays':'2','amount':'500','payment_percent':'40','activities':['Readiness report'],'responsible_consultant_id':lead_id,'description':'Assess current process'}); assert milestone.status_code==201 and milestone.json()['planned_mandays']=='2.00' and milestone.json()['responsible_consultant_id']==lead_id
        assert client.post(f'/api/job-cards/{card_id}/milestones',headers=ah,json={'code':'M1','name':'Duplicate milestone','planned_visits':1}).status_code==409
        mid=milestone.json()['id']
        activity_type=client.get('/api/activity-types',headers=ch);assert activity_type.status_code==200 and activity_type.json()
        custom_type=client.put('/api/activity-types/CLIENT_AUDIT',headers=ah,json={'code':'CLIENT_AUDIT','name':'Client audit','active':True});assert custom_type.status_code==200
        locked_activity=client.post(f'/api/job-cards/{card_id}/activities',headers=ch,json={'milestone_id':mid,'activity_type_id':activity_type.json()[0]['id'],'name':'Locked activity','description':'Should remain payment gated.'});assert locked_activity.status_code==423
        lead_id=next(a['user_id'] for a in client.get(f'/api/job-cards/{card_id}/assignments',headers=ch).json() if a['assignment_type']=='LEAD_CONSULTANT')
        invalid_team=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-25','hours':1,'minutes':0,'work_details':'Invalid team assignment','rating_code':'A','consultant_id':1}); assert invalid_team.status_code==422
        marketing=client.post('/api/auth/login',json={'email':'marketing@dmastru.local','password':'Marketing@123'}).json()['access_token']; kh={'Authorization':'Bearer '+marketing}
        locked=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-25','hours':1,'minutes':0,'work_details':'Project gate validation','rating_code':'A'}); assert locked.status_code==423
        denied=client.post(f'/api/job-cards/{card_id}/payments',headers=ch,json={'description':'Unauthorized invoice','amount_due':'100'}); assert denied.status_code==403
        initial=client.post(f'/api/job-cards/{card_id}/payments',headers=kh,json={'description':'Initial project payment','amount_due':'1000'}); assert initial.status_code==201 and initial.json()['invoice_number'].startswith('INV-')
        part=client.post(f"/api/payments/{initial.json()['id']}/confirm",headers=kh,json={'amount':'400','external_reference':'UTR-INIT-001'}); assert part.status_code==200 and part.json()['status']=='PARTIALLY_PAID'
        still_locked=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-25','hours':1,'minutes':0,'work_details':'Partial payment gate validation','rating_code':'A'}); assert still_locked.status_code==423
        paid=client.post(f"/api/payments/{initial.json()['id']}/confirm",headers=kh,json={'amount':'600','external_reference':'UTR-INIT-002'}); assert paid.status_code==200 and paid.json()['status']=='PAID'
        receipts=client.get(f"/api/payments/{initial.json()['id']}/receipts",headers=ch); assert receipts.status_code==200 and len(receipts.json())==2
        assert client.post(f"/api/payments/{initial.json()['id']}/confirm",headers=kh,json={'amount':'1','external_reference':'UTR-INIT-001'}).status_code==409
        linked_doc=client.post(f"/api/records/PAYMENT/{initial.json()['id']}/documents",headers=kh,json={'document_type':'PAYMENT_PROOF','file_name':'payment-proof.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK','remarks':'Bank transfer proof'});assert linked_doc.status_code==201 and linked_doc.json()['payment_id']==initial.json()['id']
        workspace=client.get(f'/api/job-cards/{card_id}/workspace',headers=ch);assert workspace.status_code==200 and any(row['id']==linked_doc.json()['id'] for row in workspace.json()['record_documents'])
        assert client.get(f"/api/record-documents/{linked_doc.json()['id']}/download",headers=ch).content.startswith(b'%PDF-')
        milestone_invoice=client.post(f'/api/job-cards/{card_id}/payments',headers=kh,json={'description':'M1 milestone payment','amount_due':'500','milestone_id':mid}); assert milestone_invoice.status_code==201
        milestone_paid=client.post(f"/api/payments/{milestone_invoice.json()['id']}/confirm",headers=kh,json={'amount':'500','external_reference':'UTR-M1-001'}); assert milestone_paid.status_code==200 and milestone_paid.json()['status']=='PAID'
        draft=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-25','start_time':'09:00:00','end_time':'15:30:00','location':'Client plant, Pune','findings':'Baseline data validated.','hours':6,'minutes':30,'work_details':'Assessment completed','rating_code':'A'}); assert draft.status_code==201 and draft.json()['status']=='DRAFT' and draft.json()['working_minutes']==390 and draft.json()['location']=='Client plant, Pune' and draft.json()['findings']=='Baseline data validated.'
        assert draft.json()['consultant_id']==lead_id
        activity=client.post(f'/api/job-cards/{card_id}/activities',headers=ch,json={'milestone_id':mid,'activity_type_id':activity_type.json()[0]['id'],'name':'Client process assessment','description':'Recorded detailed process findings.','start_date':'2026-08-25','end_date':'2026-08-25','duration_minutes':390,'status':'IN_PROGRESS','visit_id':draft.json()['id']});assert activity.status_code==201 and activity.json()['mandays']=='0.8125'
        activity_file=client.post(f"/api/activities/{activity.json()['id']}/attachments",headers=ch,json={'file_name':'activity-notes.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK'});assert activity_file.status_code==201
        activity_download=client.get(f"/api/activity-attachments/{activity_file.json()['id']}/download",headers=ch);assert activity_download.status_code==200 and activity_download.content.startswith(b'%PDF-')
        activity=client.patch(f"/api/activities/{activity.json()['id']}",headers=ch,json={'status':'COMPLETED'});assert activity.status_code==200 and activity.json()['status']=='COMPLETED'
        assert client.get(f'/api/job-cards/{card_id}/activities',headers=ch).json()[0]['id']==activity.json()['id']
        assert client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json()['completed_minutes']==0
        submitted=client.post(f"/api/visits/{draft.json()['id']}/submit",headers=ch); assert submitted.status_code==200
        summary=client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json(); assert summary['completed_minutes']==390 and float(summary['completed_md'])==0.8125
        milestones=client.get(f'/api/job-cards/{card_id}/milestones',headers=ch).json(); assert milestones[0]['actual_visits']==1 and float(milestones[0]['progress'])==50
        m2=client.post(f'/api/job-cards/{card_id}/milestones',headers=ah,json={'code':'M2','name':'Implementation','planned_visits':2,'start_date':'2026-08-27','closing_date':'2026-08-29','payment_date':'2026-08-30','portal_report_submission_date':'2026-08-31'}); assert m2.status_code==201 and m2.json()['payment_date']=='2026-08-30'
        m2_invoice=client.post(f'/api/job-cards/{card_id}/payments',headers=kh,json={'description':'M2 milestone payment','amount_due':'300','milestone_id':m2.json()['id']}); assert m2_invoice.status_code==201
        assert client.post(f"/api/payments/{m2_invoice.json()['id']}/confirm",headers=kh,json={'amount':'300','external_reference':'UTR-M2-001'}).status_code==200
        for visit_date,hours in (('2026-08-27',8),('2026-08-28',4)):
            visit=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':m2.json()['id'],'visit_date':visit_date,'hours':hours,'minutes':0,'work_details':'Precision regression visit','rating_code':'A'}); assert visit.status_code==201
            assert client.post(f"/api/visits/{visit.json()['id']}/submit",headers=ch).status_code==200
        exact=client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json(); assert exact['completed_minutes']==1110 and float(exact['completed_md'])==2.3125 and float(exact['remaining_md'])==11.6875

    def test_role_specific_dashboards_are_data_driven():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        marketing=client.post('/api/auth/login',json={'email':'marketing@dmastru.local','password':'Marketing@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ad=client.get('/api/dashboard/admin',headers={'Authorization':'Bearer '+admin}); assert ad.status_code==200 and ad.json()['kpis']['total_users']>=4
        assert client.get('/api/dashboard/management',headers={'Authorization':'Bearer '+admin}).status_code==403
        mk_headers={'Authorization':'Bearer '+marketing}
        assert client.get('/api/dashboard/marketing',headers=mk_headers).status_code==200
        assert client.get('/api/dashboard/admin',headers=mk_headers).status_code==403
        assert client.get('/api/dashboard/management',headers=mk_headers).status_code==403
        assert client.get('/api/dashboard/user',headers=mk_headers).status_code==403
        assert client.get('/api/reports/payments',headers=mk_headers).status_code==200
        assert client.get('/api/reports/projects',headers=mk_headers).status_code==403
        assert client.get('/api/reports/projects/export/csv',headers=mk_headers).status_code==403
        mg=client.get('/api/dashboard/management',headers={'Authorization':'Bearer '+management}); assert mg.status_code==200 and float(mg.json()['kpis']['completed_md'])==2.3125
        assert {'pending_tasks','overdue_tasks','planned_upcoming_visits','completed_site_visits','milestone_payments_pending'}<=mg.json()['kpis'].keys()
        assert {'M1','M2','M3'}<=mg.json()['milestone_progress'].keys() and mg.json()['consultant_workload'] and mg.json()['msme_progress']
        assert any(row['number'].startswith('DMJC-2026-') for row in mg.json()['recent_job_cards'])
        assert len(mg.json()['milestone_summary'])==2 and sum(row['completed_visits'] for row in mg.json()['milestone_summary'])==3
        user=client.get('/api/dashboard/user',headers={'Authorization':'Bearer '+consultant}); assert user.status_code==200 and user.json()['kpis']['total_projects']==1
        assert len(user.json()['recent_job_cards'])==1 and user.json()['recent_job_cards'][0]['number'].startswith('DMJC-2026-') and len(user.json()['projects'])<=5
        assert float(user.json()['kpis']['actual_mandays'])==2.3125 and {'my_actions','today_visits','upcoming_visits','pending_submissions','returned_items','planned_mandays','remaining_mandays'}<=user.json()['kpis'].keys()
        assert client.get('/api/dashboard/admin',headers={'Authorization':'Bearer '+consultant}).status_code==403

    def test_documents_and_sequential_approval_workflow():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        marketing=client.post('/api/auth/login',json={'email':'marketing@dmastru.local','password':'Marketing@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        unit=client.post('/api/auth/login',json={'email':'unit.rep@dmastru.local','password':'UnitRep@123'}).json()['access_token']
        director=client.post('/api/auth/login',json={'email':'director@dmastru.local','password':'Director@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; mh={'Authorization':'Bearer '+management}; kh={'Authorization':'Bearer '+marketing}; ch={'Authorization':'Bearer '+consultant}; uh={'Authorization':'Bearer '+unit}; dh={'Authorization':'Bearer '+director}
        card_id=client.get('/api/job-cards',headers=ah).json()[0]['id']; mid=client.get(f'/api/job-cards/{card_id}/milestones',headers=ah).json()[0]['id']
        draft=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-26','hours':8,'minutes':0,'work_details':'Second visit','rating_code':'B'}); assert draft.status_code==201
        assert client.post(f"/api/visits/{draft.json()['id']}/submit",headers=ch).status_code==200
        base={'file_name':'report.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK'}
        case=client.post(f'/api/job-cards/{card_id}/documents',headers=ch,json={**base,'document_type':'Case Study'}); assert case.status_code==201 and case.json()['version']==1
        tech=client.post(f'/api/job-cards/{card_id}/documents',headers=ch,json={**base,'document_type':'Technology Recommendation'}); assert tech.status_code==201
        assert client.post(f"/api/job-cards/{card_id}/approvals/CONSULTANT_CONFIRMATION",headers=ch,json={'decision':'APPROVED'}).status_code==422
        assert client.post(f"/api/documents/{case.json()['id']}/review",headers=ah,json={'decision':'APPROVED','comment':'Reviewed by admin'}).status_code==200
        assert client.post(f"/api/documents/{tech.json()['id']}/review",headers=mh,json={'decision':'APPROVED','comment':'Reviewed by management'}).status_code==200
        extra=client.post(f'/api/job-cards/{card_id}/documents',headers=ch,json={**base,'document_type':'Other Supporting Document'}); assert extra.status_code==201
        returned_document=client.post(f"/api/documents/{extra.json()['id']}/review",headers=ah,json={'decision':'RETURNED','comment':'Please correct and resubmit a clearer copy'});assert returned_document.status_code==200 and returned_document.json()['status']=='RETURNED' and returned_document.json()['reviewed_by']==1 and returned_document.json()['reviewed_at']
        replacement=client.post(f'/api/job-cards/{card_id}/documents',headers=ch,json={**base,'document_type':'Other Supporting Document'}); assert replacement.status_code==201 and replacement.json()['version']==2 and replacement.json()['status']=='RESUBMITTED'
        document_queue=client.get('/api/documents/review-queue?page=1&page_size=1',headers=mh); assert document_queue.status_code==200 and document_queue.json()['total']==1 and document_queue.json()['items'][0]['id']==replacement.json()['id']
        assert client.get('/api/documents/review-queue?page=2&page_size=1',headers=mh).json()['items']==[]
        visit_queue=client.get('/api/visits/review-queue?page=1&page_size=1',headers=mh); assert visit_queue.status_code==200 and visit_queue.json()['total']>=2 and len(visit_queue.json()['items'])==1
        assert client.get('/api/documents/review-queue',headers=ch).status_code==403
        assert client.post(f"/api/job-cards/{card_id}/approvals/DIRECTOR_REVIEW",headers=mh,json={'decision':'APPROVED'}).status_code==403
        assert client.get(f'/api/job-cards/{card_id}/milestones',headers=ch).json()[0]['status']=='READY_FOR_REVIEW'
        milestone_submit=client.post(f'/api/milestones/{mid}/submit-review',headers=ch); assert milestone_submit.status_code==201 and milestone_submit.json()['cycle']==1
        assert client.post(f'/api/milestones/{mid}/submit-review',headers=ch).status_code==409
        assert client.get('/api/milestones/management-review-queue',headers=mh).status_code==200
        reviewed=client.post(f'/api/milestones/{mid}/management-review',headers=mh,json={'decision':'APPROVED','comment':'Visits and supporting records checked'}); assert reviewed.status_code==201
        assert client.get(f'/api/job-cards/{card_id}/milestones',headers=ch).json()[0]['status']=='AWAITING_UNIT_CONFIRMATION'
        assert client.get('/api/milestones/unit-confirmation-queue',headers=uh).status_code==200
        confirmed=client.post(f'/api/milestones/{mid}/unit-confirmation',headers=uh,json={'decision':'APPROVED','comment':'Milestone confirmed'}); assert confirmed.status_code==201
        assert client.get(f'/api/job-cards/{card_id}/milestones',headers=ch).json()[0]['status']=='COMPLETED'
        for other in client.get(f'/api/job-cards/{card_id}/milestones',headers=ch).json():
            if other['status']=='COMPLETED' or other['actual_visits']<other['planned_visits']: continue
            submitted=client.post(f"/api/milestones/{other['id']}/submit-review",headers=ch); assert submitted.status_code==201
            returned=client.post(f"/api/milestones/{other['id']}/management-review",headers=mh,json={'decision':'RETURNED','comment':'Please add missing milestone notes'}); assert returned.status_code==201
            resubmitted=client.post(f"/api/milestones/{other['id']}/submit-review",headers=ch); assert resubmitted.status_code==201 and resubmitted.json()['cycle']==2
            assert client.post(f"/api/milestones/{other['id']}/management-review",headers=mh,json={'decision':'APPROVED','comment':'Reviewed after correction'}).status_code==201
            assert client.post(f"/api/milestones/{other['id']}/unit-confirmation",headers=uh,json={'decision':'APPROVED','comment':'Confirmed'}).status_code==201
        assert client.post(f"/api/job-cards/{card_id}/approvals/CONSULTANT_CONFIRMATION",headers=ch,json={'decision':'APPROVED'}).status_code==201
        assert client.post(f"/api/job-cards/{card_id}/approvals/UNIT_REPRESENTATIVE",headers=mh,json={'decision':'APPROVED'}).status_code==403
        assert client.post(f"/api/job-cards/{card_id}/approvals/UNIT_REPRESENTATIVE",headers=uh,json={'decision':'APPROVED'}).status_code==201
        closure={'client_confirmation':'Client confirmed the completed work by email.','problem':'Production workflow had manual gaps.','requirement':'Improve work tracking and reporting.','solution':'Structured consulting and process changes.','implementation':'Three milestone delivery with staff coaching.','benefits':'Improved consistency and visibility.','results':'Client adopted the recommended process.','feedback_rating':5,'feedback':'The project met our expectations.','testimonial':'A helpful and well managed engagement.','publication_permission':False}
        assert client.put(f'/api/job-cards/{card_id}/closure',headers=kh,json=closure).status_code==200
        final=client.post(f"/api/job-cards/{card_id}/approvals/DIRECTOR_REVIEW",headers=dh,json={'decision':'APPROVED'}); assert final.status_code==201
        assert any('closure' in note['message'].lower() for note in client.get('/api/notifications',headers=ch).json())
        assert client.get(f'/api/job-cards/{card_id}',headers=mh).json()['status']=='CLOSURE_PENDING'
        assert client.post(f'/api/job-cards/{card_id}/closure/complete',headers=mh).status_code==403
        closed=client.post(f'/api/job-cards/{card_id}/closure/complete',headers=ah); assert closed.status_code==200 and closed.json()['certificate_number'].startswith('CERT-')
        certificate=client.get(f'/api/job-cards/{card_id}/closure/certificate',headers=mh); assert certificate.status_code==200 and certificate.content.startswith(b'%PDF-')
        assert client.get(f'/api/job-cards/{card_id}',headers=mh).json()['status']=='COMPLETED'
        immutable=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-27','hours':1,'minutes':0,'work_details':'Must be rejected after completion','rating_code':'A'}); assert immutable.status_code==409
        assert client.patch(f'/api/job-cards/{card_id}',headers=ah,json={'location':'Blocked change'}).status_code==409
        reopened=client.post(f'/api/job-cards/{card_id}/reopen',headers=ah,json={'reason':'QA correction required'}); assert reopened.status_code==200 and reopened.json()['status']=='IN_PROGRESS'

    def test_notifications_and_immutable_activity_logs():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant}
        notifications=client.get('/api/notifications',headers=ch); assert notifications.status_code==200 and notifications.json()
        first=notifications.json()[0]; marked=client.post(f"/api/notifications/{first['id']}/read",headers=ch); assert marked.status_code==200 and marked.json()['read_at']
        activity=client.get('/api/activity',headers=ch); assert activity.status_code==200 and any(row['action']=='VISIT_SUBMITTED' for row in activity.json())
        audit=client.get('/api/audit-logs',headers=ah); assert audit.status_code==200 and any(row['entity_type']=='APPROVAL' for row in audit.json())
        assert client.get('/api/audit-logs',headers=ch).status_code==403

    def test_reports_and_authorized_csv_xlsx_exports():
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        mh={'Authorization':'Bearer '+management}; ch={'Authorization':'Bearer '+consultant}
        projects=client.get('/api/reports/projects',headers=mh); assert projects.status_code==200 and projects.json()['items'][0]['Job Card'].startswith('DMJC-2026-') and projects.json()['total']==1
        visit_page=client.get('/api/reports/visits?page=1&page_size=1',headers=mh); assert visit_page.status_code==200 and visit_page.json()['page_size']==1 and len(visit_page.json()['items'])==1 and visit_page.json()['total']>=3
        assert visit_page.json()['items'][0]['Consultant']=='Consultant Demo' and 'Co-Consultant' in visit_page.json()['items'][0]
        milestones=client.get('/api/reports/milestones',headers=mh).json()['items']; assert any(m['Payment Date']=='2026-08-30' and m['Portal Report Submission Date']=='2026-08-31' for m in milestones)
        consultants=client.get('/api/reports/consultants',headers=mh); assert consultants.status_code==200 and float(consultants.json()['items'][0]['Mandays'])==3.3125
        tasks=client.get('/api/reports/tasks',headers=mh); assert tasks.status_code==200
        project_row=projects.json()['items'][0]
        filtered=client.get(f"/api/reports/projects?msme_id={project_row['MSME ID']}&project_status={project_row['Project Status']}",headers=mh); assert filtered.status_code==200 and filtered.json()['total']==1 and filtered.json()['items'][0]['Job Card ID']==project_row['Job Card ID']
        csv=client.get('/api/reports/projects/export/csv',headers=mh); assert csv.status_code==200 and b'DMJC-2026-' in csv.content and csv.headers['content-type'].startswith('text/csv')
        xlsx=client.get('/api/reports/milestones/export/xlsx',headers=mh); assert xlsx.status_code==200 and is_zipfile(BytesIO(xlsx.content))
        pdf=client.get('/api/reports/projects/export/pdf',headers=mh); assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
        assert client.get('/api/reports/projects',headers=ch).status_code==403

    def test_health_security_headers_and_consistent_errors():
        health=client.get('/health'); assert health.status_code==200 and health.json()['data']['status']=='ok'
        assert health.headers['x-content-type-options']=='nosniff' and health.headers['x-frame-options']=='DENY'
        denied=client.get('/api/auth/me'); assert denied.status_code==401 and denied.json()['error']['code']=='AUTHENTICATION_ERROR'
        invalid=client.post('/api/auth/login',json={'email':'not-an-email','password':'x'}); assert invalid.status_code==422 and invalid.json()['error']['code']=='VALIDATION_ERROR'

    def test_file_signature_validation_and_rate_limit_headers():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']; consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        card_id=client.get('/api/job-cards',headers={'Authorization':'Bearer '+admin}).json()[0]['id']
        invalid=client.post(f'/api/job-cards/{card_id}/documents',headers={'Authorization':'Bearer '+consultant},json={'document_type':'Other Supporting Document','file_name':'fake.pdf','mime_type':'application/pdf','content_base64':'bm90LWEtcGRm'}); assert invalid.status_code==422

    def test_registration_profile_password_reset_refresh_and_deactivation():
        registration=client.post('/api/auth/register',json={'full_name':'New Field Consultant','email':'new.consultant@example.com','mobile':'9999999999','password':'Strong@123','confirm_password':'Strong@123'}); assert registration.status_code==201 and registration.json()['status']=='PENDING_ACTIVATION'
        assert client.post('/api/auth/login',json={'email':'new.consultant@example.com','password':'Strong@123'}).status_code==403
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']; ah={'Authorization':'Bearer '+admin}
        assert client.post(f"/api/users/{registration.json()['id']}/activate",headers=ah).status_code==200
        login=client.post('/api/auth/login',json={'email':'new.consultant@example.com','password':'Strong@123'}); assert login.status_code==200
        data=login.json(); headers={'Authorization':'Bearer '+data['access_token']}
        refreshed=client.post('/api/auth/refresh',json={'refresh_token':data['refresh_token']}); assert refreshed.status_code==200 and refreshed.json()['refresh_token']!=data['refresh_token']
        assert client.post('/api/auth/refresh',json={'refresh_token':data['refresh_token']}).status_code==401
        profile=client.patch('/api/auth/me',headers={'Authorization':'Bearer '+refreshed.json()['access_token']},json={'full_name':'Updated Consultant','mobile':'8888888888','consultant_id':'CON-7'}); assert profile.status_code==200 and profile.json()['full_name']=='Updated Consultant'
        changed=client.post('/api/auth/change-password',headers={'Authorization':'Bearer '+refreshed.json()['access_token']},json={'current_password':'Strong@123','new_password':'Changed@123','confirm_password':'Changed@123'}); assert changed.status_code==200
        assert client.post('/api/auth/login',json={'email':'new.consultant@example.com','password':'Changed@123'}).status_code==200
        reset=client.post('/api/auth/forgot-password',json={'email':'new.consultant@example.com'}); assert reset.status_code==200 and reset.json().get('development_reset_token')
        assert client.post('/api/auth/reset-password',json={'token':reset.json()['development_reset_token'],'new_password':'Reset@123','confirm_password':'Reset@123'}).status_code==200
        assert client.post('/api/auth/login',json={'email':'new.consultant@example.com','password':'Reset@123'}).status_code==200
        assert client.post(f"/api/users/{registration.json()['id']}/deactivate",headers=ah).status_code==200
        assert client.post('/api/auth/login',json={'email':'new.consultant@example.com','password':'Reset@123'}).status_code==403

    def test_operational_settings_reassignment_filters_review_and_archival():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; mh={'Authorization':'Bearer '+management}; ch={'Authorization':'Bearer '+consultant}
        master=client.post('/api/master-data',headers=ah,json={'category':'CERTIFICATE_LEVEL','code':'PLATINUM','label':'Platinum'}); assert master.status_code==201
        assert client.get('/api/master-data?category=CERTIFICATE_LEVEL',headers=mh).json()[0]['code']=='PLATINUM'
        assert client.put('/api/settings/manday_hours',headers=ah,json={'value':'8'}).status_code==200
        assert client.get('/api/settings/manday_hours',headers=ah).json()['value']=='8'
        card=client.get('/api/job-cards?search=DMJC-2026&page=1&page_size=1',headers=ah); assert card.status_code==200 and len(card.json())==1
        card_id=card.json()[0]['id']
        updated=client.patch(f'/api/job-cards/{card_id}',headers=ah,json={'location':'Mumbai','required_md':'15'}); assert updated.status_code==200 and updated.json()['required_hours']=='120.00'
        docs=client.get(f'/api/job-cards/{card_id}/documents',headers=mh).json(); download=client.get(f"/api/documents/{docs[0]['id']}/download",headers=mh); assert download.status_code==200 and download.headers['content-disposition'].startswith('attachment;')
        visits=client.get(f'/api/job-cards/{card_id}/visits',headers=mh).json(); rejected=client.post(f"/api/visits/{visits[0]['id']}/reject",headers=mh,json={'reason':'Clarify findings'}); assert rejected.status_code==200 and rejected.json()['status']=='RETURNED'
        resubmitted=client.post(f"/api/visits/{visits[0]['id']}/resubmit",headers=ch); assert resubmitted.status_code==200 and resubmitted.json()['status']=='SUBMITTED'
        approved=client.post(f"/api/visits/{visits[0]['id']}/review",headers=mh,json={'decision':'APPROVED','comment':'Reviewed'}); assert approved.status_code==200 and approved.json()['status']=='APPROVED'
        history=client.get(f"/api/visits/{visits[0]['id']}/review-history",headers=mh); assert history.status_code==200 and [event['decision'] for event in history.json()]==['REJECTED','APPROVED']
        assert client.delete(f"/api/visits/{visits[0]['id']}",headers=ch).status_code==404
        assert any(v['id']==visits[0]['id'] and v['status']=='APPROVED' for v in client.get(f'/api/job-cards/{card_id}/visits',headers=mh).json())

    def test_admin_user_listing_is_backend_protected():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        users=client.get('/api/users',headers={'Authorization':'Bearer '+admin}); assert users.status_code==200 and len(users.json())>=3
        assert client.get('/api/users',headers={'Authorization':'Bearer '+consultant}).status_code==403

    def test_admin_user_edit_role_password_reset_unlock_and_audit():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']; ah={'Authorization':'Bearer '+admin}
        created=client.post('/api/users',headers=ah,json={'full_name':'Workflow Reviewer','email':'reviewer@example.com','password':'Strong@123','role':'END_USER'}).json()
        assert client.post(f"/api/users/{created['id']}/activate",headers=ah).status_code==200
        updated=client.patch(f"/api/users/{created['id']}",headers=ah,json={'full_name':'Unit Reviewer','role':'UNIT_REPRESENTATIVE'}); assert updated.status_code==200 and updated.json()['role']=='UNIT_REPRESENTATIVE'
        assert client.post(f"/api/users/{created['id']}/reset-password",headers=ah,json={'new_password':'NewStrong@123'}).status_code==200
        assert client.post('/api/auth/login',json={'email':'reviewer@example.com','password':'NewStrong@123'}).status_code==200
        for _ in range(5): client.post('/api/auth/login',json={'email':'reviewer@example.com','password':'wrong'})
        assert client.post('/api/auth/login',json={'email':'reviewer@example.com','password':'NewStrong@123'}).status_code==403
        assert client.post(f"/api/users/{created['id']}/unlock",headers=ah).status_code==200
        assert client.post('/api/auth/login',json={'email':'reviewer@example.com','password':'NewStrong@123'}).status_code==200
        logs=client.get('/api/audit-logs',headers=ah).json(); assert any(x['action']=='USER_UPDATED' and x['entity_id']==created['id'] for x in logs) and any(x['action']=='LOGIN_FAILED' for x in logs)

    def test_msme_and_job_card_backend_filters():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']; ah={'Authorization':'Bearer '+admin}
        msme=client.get('/api/msmes?search=Acme&certificate_level=Gold',headers=ah).json()[0]
        updated=client.patch(f"/api/msmes/{msme['id']}",headers=ah,json={'city':'Pune','zed_id':'ZED-100'}); assert updated.status_code==200 and updated.json()['city']=='Pune'
        assert len(client.get('/api/msmes?search=ZED-100&certificate_level=Gold',headers=ah).json())==1
        card=client.get('/api/job-cards?search=Acme&zed_id=ZED-100&certificate_level=Gold',headers=ah); assert card.status_code==200 and card.json()[0]['number'].startswith('DMJC-2026-')
        assert client.get('/api/job-cards?delayed=false&overrun=false',headers=ah).status_code==200

    def test_visit_attachment_upload_and_authorized_download():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']; consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant}; card_id=client.get('/api/job-cards',headers=ah).json()[0]['id']; milestone_id=client.get(f'/api/job-cards/{card_id}/milestones',headers=ah).json()[0]['id']
        visit=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':milestone_id,'visit_date':'2026-08-29','hours':1,'minutes':0,'work_details':'Attachment validation','rating_code':'A','attachment_name':'evidence.pdf','attachment_mime_type':'application/pdf','attachment_base64':'JVBERi0xLjQKJXRlc3QK'}); assert visit.status_code==201 and visit.json()['attachment_name']=='evidence.pdf'
        attachment=client.get(f"/api/visits/{visit.json()['id']}/attachment",headers=ch); assert attachment.status_code==200 and attachment.content.startswith(b'%PDF-')

    def test_acceptance_admin_consultant_management_journey():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()
        unit=client.post('/api/auth/login',json={'email':'unit.rep@dmastru.local','password':'UnitRep@123'}).json()
        director=client.post('/api/auth/login',json={'email':'director@dmastru.local','password':'Director@123'}).json()
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant['access_token']}; mh={'Authorization':'Bearer '+management}
        msme=client.post('/api/msmes',headers=ah,json={'name':'Acceptance Journey Ltd','msme_code':'MSME-ACCEPTANCE','zed_id':'ZED-ACCEPTANCE','certificate_level':'Silver'})
        assert msme.status_code==201
        card=client.post('/api/job-cards',headers=ah,json={'msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-10-01','project_end_date':'2026-12-31','required_md':'14','lead_consultant_id':consultant['user']['id'],'unit_representative_id':unit['user']['id'],'director_id':director['user']['id']})
        assert card.status_code==201 and card.json()['required_hours']=='112.00' and card.json()['status']=='AWAITING_PAYMENT' and card.json()['number'].startswith('DMJC-2026-')
        card_id=card.json()['id']
        assert any(row['number']==card.json()['number'] for row in client.get('/api/job-cards',headers=ch).json())
        milestones=[]
        for code,name,count in [('M1','Initial assessment',2),('M2','Implementation',1),('M3','Final review',1)]:
            result=client.post(f'/api/job-cards/{card_id}/milestones',headers=ah,json={'code':code,'name':name,'planned_visits':count,'planned_mandays':1,'responsible_consultant_id':consultant['user']['id']})
            assert result.status_code==201 and result.json()['responsible_consultant_id']==consultant['user']['id']
            milestones.append(result.json())
        milestone=milestones[0]
        milestone_by_code={row['code']:row for row in milestones}
        for code in ('M1','M2','M3'):
            blocked=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':milestone_by_code[code]['id'],'visit_date':'2026-10-05','hours':1,'minutes':0,'work_details':f'{code} must stay payment locked','rating_code':'A'})
            assert blocked.status_code==423
        marketer=client.post('/api/auth/login',json={'email':'marketing@dmastru.local','password':'Marketing@123'}).json()['access_token']; kh={'Authorization':'Bearer '+marketer}
        initial=client.post(f'/api/job-cards/{card_id}/payments',headers=kh,json={'description':'Initial project payment','amount_due':'2500'});assert initial.status_code==201
        assert client.post(f"/api/payments/{initial.json()['id']}/confirm",headers=kh,json={'amount':'2500','external_reference':'UTR-ACCEPT-INIT'}).status_code==200
        for code in ('M1','M2','M3'):
            still_locked=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':milestone_by_code[code]['id'],'visit_date':'2026-10-05','hours':1,'minutes':0,'work_details':f'{code} needs its own payment','rating_code':'A'})
            assert still_locked.status_code==423
        m1=client.post(f'/api/job-cards/{card_id}/payments',headers=kh,json={'description':'M1 payment','amount_due':'1000','milestone_id':milestone['id']});assert m1.status_code==201
        assert client.post(f"/api/payments/{m1.json()['id']}/confirm",headers=kh,json={'amount':'1000','external_reference':'UTR-ACCEPT-M1'}).status_code==200
        activity_type=client.get('/api/activity-types',headers=ch).json()[0]
        tasks={}
        for code in ('M1','M2','M3'):
            if code!='M1':
                premature=client.post(f'/api/job-cards/{card_id}/activities',headers=ch,json={'milestone_id':milestone_by_code[code]['id'],'activity_type_id':activity_type['id'],'name':f'{code} blocked task','description':'Should remain locked until this stage is paid.'})
                assert premature.status_code==423
            elif client.post(f'/api/job-cards/{card_id}/activities',headers=ch,json={'milestone_id':milestone_by_code[code]['id'],'activity_type_id':activity_type['id'],'name':f'{code} assigned task','description':'Consultant task for the paid milestone.'}).status_code==201:
                tasks[code]=True
            else:
                raise AssertionError('Paid M1 task could not be created.')
        for code in ('M2','M3'):
            invoice=client.post(f'/api/job-cards/{card_id}/payments',headers=kh,json={'description':f'{code} milestone payment','amount_due':'500','milestone_id':milestone_by_code[code]['id']})
            assert invoice.status_code==201
            assert client.post(f"/api/payments/{invoice.json()['id']}/confirm",headers=kh,json={'amount':'500','external_reference':f'UTR-ACCEPT-{code}'}).status_code==200
            task=client.post(f'/api/job-cards/{card_id}/activities',headers=ch,json={'milestone_id':milestone_by_code[code]['id'],'activity_type_id':activity_type['id'],'name':f'{code} assigned task','description':'Consultant task unlocked by this milestone payment.'})
            assert task.status_code==201 and task.json()['assigned_user_id']==consultant['user']['id']
            tasks[code]=True
        assert set(tasks)=={'M1','M2','M3'}
        visit=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':milestone['id'],'visit_date':'2026-10-05','hours':6,'minutes':30,'work_details':'Completed baseline assessment','rating_code':'A'})
        assert visit.status_code==201 and visit.json()['status']=='DRAFT'
        assert client.post(f"/api/visits/{visit.json()['id']}/submit",headers=ch).status_code==200
        summary=client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json()
        assert summary['completed_minutes']==390 and float(summary['completed_md'])==0.8125
        assert float(summary['remaining_md'])==13.1875 and float(summary['progress'])==5.8
        planned=client.post('/api/planned-visits',headers=ch,json={'job_card_id':card_id,'milestone_id':milestone['id'],'visit_date':'2026-10-13','start_time':'10:00','expected_duration_minutes':120,'location':'Pune','objective':'On-site implementation follow-up'})
        assert planned.status_code==201,planned.text
        for headers in (ah,mh):
            visible=client.get(f"/api/planned-visits?job_card_id={card_id}",headers=headers)
            assert visible.status_code==200 and any(row['id']==planned.json()['id'] and row['status']=='PLANNED' for row in visible.json())
        actual_visit=client.patch(f"/api/planned-visits/{planned.json()['id']}",headers=ch,json={'status':'COMPLETED','actual_date':'2026-10-13','actual_start_time':'10:15','actual_duration_minutes':105,'outcome':'Reviewed implementation findings and next steps.'})
        assert actual_visit.status_code==200 and actual_visit.json()['actual_duration_minutes']==105
        for headers in (ah,mh):
            visible=client.get(f"/api/planned-visits?job_card_id={card_id}",headers=headers)
            assert any(row['id']==planned.json()['id'] and row['status']=='COMPLETED' for row in visible.json())
        updated_mandays=client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json()
        assert updated_mandays['completed_minutes']==495 and float(updated_mandays['completed_md'])==1.0313
        draft_doc=client.post(f'/api/records/MILESTONE/{milestone["id"]}/documents',headers=ch,json={'document_type':'M1 Evidence','file_name':'m1-evidence.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK','remarks':'Baseline evidence','status':'DRAFT'})
        assert draft_doc.status_code==201 and draft_doc.json()['status']=='DRAFT'
        submitted_doc=client.post(f"/api/record-documents/{draft_doc.json()['id']}/submit",headers=ch)
        assert submitted_doc.status_code==200 and submitted_doc.json()['status']=='SUBMITTED'
        reviewing=client.post(f"/api/record-documents/{draft_doc.json()['id']}/review",headers=mh,json={'decision':'UNDER_REVIEW','comment':'Review started.'})
        assert reviewing.status_code==200 and reviewing.json()['status']=='UNDER_REVIEW'
        returned_doc=client.post(f"/api/record-documents/{draft_doc.json()['id']}/review",headers=mh,json={'decision':'RETURNED','comment':'Add a signed approval sheet.'})
        assert returned_doc.status_code==200 and returned_doc.json()['status']=='RETURNED'
        revision=client.post(f'/api/records/MILESTONE/{milestone["id"]}/documents',headers=ch,json={'document_type':'M1 Evidence','file_name':'m1-evidence-v2.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK','remarks':'Updated signed evidence'})
        assert revision.status_code==201 and revision.json()['version']==2 and revision.json()['status']=='SUBMITTED'
        dashboard=client.get('/api/dashboard/management',headers=mh)
        assert dashboard.status_code==200 and any(row['number']==card.json()['number'] for row in dashboard.json()['recent_job_cards'])
        assert {'M1','M2','M3'}<=dashboard.json()['milestone_progress'].keys() and dashboard.json()['consultant_workload']
        assert any(row['id']==planned.json()['id'] for row in client.get('/api/planned-visits?start_date=2026-10-12',headers=mh).json())
        assert client.get(f'/api/job-cards/{card_id}',headers=mh).status_code==200

    def test_marketing_enquiry_ticket_followup_and_access_control():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        marketing=client.post('/api/auth/login',json={'email':'marketing@dmastru.local','password':'Marketing@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; kh={'Authorization':'Bearer '+marketing}; ch={'Authorization':'Bearer '+consultant}
        depts=client.get('/api/departments',headers=kh); assert depts.status_code==200 and any(d['code']=='MARKETING' for d in depts.json())
        enquiry=client.post('/api/enquiries',headers=kh,json={'company_name':'CRM Acceptance Ltd','contact_person':'Asha Rao','email':'asha@crm-acceptance.example','requirement':'Needs readiness assessment','source':'Referral','package_code':'SILVER','expected_scope':'Readiness review and implementation plan','expected_commercial_amount':'3750'}); assert enquiry.status_code==201 and enquiry.json()['number'].startswith('DMENQ-') and enquiry.json()['expected_commercial_amount']=='3750.00'
        enquiry_audit=client.get(f"/api/audit-logs?entity_type=ENQUIRY&search={enquiry.json()['id']}",headers=ah);assert enquiry_audit.status_code==200 and any(item['entity_ref']==enquiry.json()['id'] for item in enquiry_audit.json())
        enquiry_id=enquiry.json()['id']
        matches=client.get('/api/enquiries/matches?company_name=CRM%20Acceptance%20Ltd',headers=kh); assert matches.status_code==200 and any(row['id']==enquiry_id for row in matches.json())
        assert client.get('/api/enquiries',headers=ch).status_code==403
        follow=client.post(f'/api/enquiries/{enquiry_id}/follow-ups',headers=kh,json={'follow_up_type':'CALL','person_contacted':'Asha Rao','notes':'Discussed assessment scope.','next_follow_up_date':'2026-10-12T09:00:00Z'}); assert follow.status_code==201
        assert client.get(f'/api/enquiries/{enquiry_id}/follow-ups',headers=kh).json()[0]['notes']=='Discussed assessment scope.'
        follow_queue=client.get('/api/marketing/follow-ups?bucket=all',headers=kh);assert follow_queue.status_code==200 and any(row['enquiry_id']==enquiry_id for row in follow_queue.json()['items'])
        follow_file=client.post(f'/api/enquiries/{enquiry_id}/follow-ups',headers=kh,json={'follow_up_type':'EMAIL','notes':'Sent the service overview.','attachment_name':'overview.pdf','attachment_mime_type':'application/pdf','attachment_base64':'JVBERi0xLjQKJXRlc3QK'});assert follow_file.status_code==201
        assert client.get(f"/api/enquiry-follow-ups/{follow_file.json()['id']}/attachment",headers=kh).content.startswith(b'%PDF-')
        enquiry_doc=client.post(f'/api/enquiries/{enquiry_id}/documents',headers=kh,json={'document_type':'CLIENT_BRIEF','file_name':'brief.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK'});assert enquiry_doc.status_code==201
        assert client.get(f"/api/enquiry-documents/{enquiry_doc.json()['id']}/download",headers=kh).content.startswith(b'%PDF-')
        ticket=client.post('/api/tickets',headers=kh,json={'title':'Prepare client proposal','description':'Draft proposal for the new enquiry.','ticket_type':'INTERNAL_ACTION','priority':'HIGH','enquiry_id':enquiry_id}); assert ticket.status_code==201 and ticket.json()['number'].startswith('DMTKT-')
        search=client.get('/api/search',headers=kh,params={'q':'Prepare client proposal'});assert search.status_code==200 and any(r['kind']=='Ticket' and r['id']==ticket.json()['id'] for r in search.json())
        marketing_dash=client.get('/api/dashboard/marketing',headers=kh);assert marketing_dash.status_code==200 and {'total_enquiries','new_enquiries','converted_enquiries','followups_upcoming','followups_overdue','payments_confirmed','payments_overdue','recent_payment_activity','open_tickets','pending_invoices','projects_awaiting_activation','milestone_payments_pending'}<=marketing_dash.json().keys() and marketing_dash.json()['total_enquiries']>=1
        ticket_id=ticket.json()['id']; relationships=client.get(f'/api/tickets/{ticket_id}/relationships',headers=kh); assert relationships.status_code==200 and relationships.json()[0]['related_enquiry_id']==enquiry_id
        workflow=client.put('/api/workflow-templates/LEAD_FOLLOWUP',headers=ah,json={'code':'LEAD_FOLLOWUP','name':'Lead follow-up','active':True,'trigger_event':'MANUAL','steps':[{'action_type':'CLIENT_CONTACT','title':'Contact lead','department_code':'MARKETING','role':'MARKETING','due_days':2},{'action_type':'QUALIFICATION','title':'Qualify lead','department_code':'MARKETING','role':'MARKETING','due_days':1,'return_to_step':1}]});assert workflow.status_code==200,workflow.text
        action=client.post('/api/actions/start',headers=kh,json={'template_code':'LEAD_FOLLOWUP','ticket_id':ticket_id});assert action.status_code==201,action.text and action.json()['action_number'].startswith('ACT-')
        departments=client.get('/api/departments',headers=ah).json();marketing_dept=next(d for d in departments if d['code']=='MARKETING')
        marketing_user=next(u for u in client.get('/api/users',headers=ah).json() if u['email']=='marketing@dmastru.local')
        transferred=client.post(f"/api/actions/{action.json()['id']}/transfer",headers=kh,json={'department_id':marketing_dept['id'],'assigned_user_id':marketing_user['id'],'reason':'Balance the follow-up queue.'});assert transferred.status_code==200 and transferred.json()['assigned_user_id']==marketing_user['id']
        assert client.post(f"/api/actions/{action.json()['id']}/transfer",headers=ch,json={'department_id':marketing_dept['id'],'reason':'Unauthorized transfer attempt'}).status_code==403
        frozen_template=client.put('/api/workflow-templates/LEAD_FOLLOWUP',headers=ah,json={'code':'LEAD_FOLLOWUP','name':'Changed while in use','active':True,'steps':[{'action_type':'OTHER','title':'Invalid unsafe change','department_code':'MARKETING','role':'MARKETING'}]});assert frozen_template.status_code==409
        claimed=client.post(f"/api/actions/{action.json()['id']}/claim",headers=kh);assert claimed.status_code==200 and claimed.json()['assigned_user_id']
        reminder_db=SessionLocal();reminder_action=reminder_db.get(WorkflowAction,UUID(action.json()['id']));reminder_action.due_at=datetime.now(timezone.utc)-timedelta(hours=1);reminder_db.commit();reminder_db.close()
        reminders=client.get('/api/notifications',headers=kh);assert reminders.status_code==200 and any('overdue' in n['message'].lower() and action.json()['action_number'] in n['message'] for n in reminders.json())
        reminder_count=sum('overdue' in n['message'].lower() and action.json()['action_number'] in n['message'] for n in reminders.json())
        assert sum('overdue' in n['message'].lower() and action.json()['action_number'] in n['message'] for n in client.get('/api/notifications',headers=kh).json())==reminder_count==1
        reminder_db=SessionLocal();reminder_action=reminder_db.get(WorkflowAction,UUID(action.json()['id']));reminder_action.due_at=datetime.now(timezone.utc)+timedelta(hours=12);reminder_db.commit();reminder_db.close()
        upcoming=client.get('/api/notifications',headers=kh);assert any('upcoming' in n['message'].lower() and action.json()['action_number'] in n['message'] for n in upcoming.json())
        reminder_db=SessionLocal();reminder_action=reminder_db.get(WorkflowAction,UUID(action.json()['id']));reminder_action.due_at=datetime.now(timezone.utc)+timedelta(days=2);reminder_db.commit();reminder_db.close()
        waiting=client.patch(f"/api/actions/{action.json()['id']}",headers=kh,json={'status':'WAITING','remarks':'Waiting for the client to respond.'});assert waiting.status_code==200 and waiting.json()['status']=='WAITING'
        resumed=client.patch(f"/api/actions/{action.json()['id']}",headers=kh,json={'status':'IN_PROGRESS'});assert resumed.status_code==200 and resumed.json()['status']=='IN_PROGRESS'
        action_file=client.post(f"/api/actions/{action.json()['id']}/attachments",headers=kh,json={'file_name':'client-brief.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK'});assert action_file.status_code==201
        assert client.get(f"/api/action-attachments/{action_file.json()['id']}/download",headers=kh).content.startswith(b'%PDF-')
        completed=client.post(f"/api/actions/{action.json()['id']}/decision",headers=kh,json={'decision':'COMPLETED'});assert completed.status_code==200 and completed.json()['status']=='COMPLETED'
        action_rows=client.get('/api/actions',headers=kh).json();assert any(r['workflow_step']==2 and r['previous_action_id']==action.json()['id'] for r in action_rows)
        second=next(r for r in action_rows if r['workflow_step']==2);assert client.post(f"/api/actions/{second['id']}/claim",headers=kh).status_code==200
        returned=client.post(f"/api/actions/{second['id']}/decision",headers=kh,json={'decision':'RETURNED','remarks':'Please attach a clearer client scope.'});assert returned.status_code==200 and returned.json()['status']=='RETURNED'
        corrections=client.get('/api/actions',headers=kh).json();assert any(r['workflow_step']==1 and r['title'].startswith('Correction:') and r['previous_action_id']==second['id'] for r in corrections)
        attachment=client.post(f'/api/tickets/{ticket_id}/attachments',headers=kh,json={'file_name':'proposal.pdf','mime_type':'application/pdf','content_base64':'JVBERi0xLjQKJXRlc3QK'});assert attachment.status_code==201
        assert client.get(f"/api/ticket-attachments/{attachment.json()['id']}/download",headers=kh).content.startswith(b'%PDF-')
        follow_ticket=client.post('/api/tickets',headers=kh,json={'title':'Confirm proposal timing','description':'Follow up on timing for this lead.','ticket_type':'FOLLOW_UP','enquiry_id':enquiry_id}); assert follow_ticket.status_code==201 and follow_ticket.json()['number'].startswith('DMTKT-') and follow_ticket.json()['number']!=ticket.json()['number']
        assert client.get(f'/api/tickets/{ticket_id}',headers=ch).status_code==404
        assert client.get('/api/tickets',headers=ah).json()['total']>=1
        missing_reason=client.patch(f'/api/tickets/{ticket_id}',headers=kh,json={'status':'REJECTED'}); assert missing_reason.status_code==422
        rejected=client.patch(f'/api/tickets/{ticket_id}',headers=kh,json={'status':'REJECTED','remarks':'Duplicate action request'}); assert rejected.status_code==200 and rejected.json()['completed_at'] is None
        qualified=client.patch(f'/api/enquiries/{enquiry_id}',headers=kh,json={'status':'QUALIFIED'}); assert qualified.status_code==200
        enquiry_update=client.patch(f'/api/enquiries/{enquiry_id}',headers=kh,json={'expected_commercial_amount':'4200','location':'Pune'});assert enquiry_update.status_code==200 and enquiry_update.json()['expected_commercial_amount']=='4200.00' and enquiry_update.json()['location']=='Pune'
        users={}
        for row in client.get('/api/users',headers=ah).json():
            if row['status']=='ACTIVE': users.setdefault(row['role'],row['id'])
        msme=client.post('/api/msmes',headers=ah,json={'name':'CRM Conversion Ltd','msme_code':'MSME-CRM-CONV','certificate_level':'Silver'}); assert msme.status_code==201
        package=client.put('/api/packages/SILVER',headers=ah,json={'code':'SILVER','name':'Silver Readiness','scope':'Structured readiness assessment and implementation support.','planned_mandays':5,'commercial_amount':3750,'project_payment_percent':20,'milestones':[{'code':'M1','name':'Assessment','planned_visits':2,'planned_mandays':2,'amount':1500,'payment_percent':40,'activities':['Site assessment']},{'code':'M2','name':'Implementation','planned_visits':3,'planned_mandays':3,'amount':1500,'payment_percent':40,'activities':['Implementation support']}],'activities':['Client kickoff'],'active':True});assert package.status_code==200,package.text
        job_data={'source_enquiry_id':enquiry_id,'project_name':'Readiness Support','package_code':'SILVER','scope':'Assessment and improvement plan','msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-10-06','project_end_date':'2026-12-31','required_md':'5','lead_consultant_id':users['END_USER'],'unit_representative_id':users['UNIT_REPRESENTATIVE'],'director_id':users['DIRECTOR']}
        assert client.post('/api/job-cards',headers=kh,json=job_data).status_code==403
        job=client.post('/api/job-cards',headers=ah,json={**job_data,'remarks':'Await initial deposit'}); assert job.status_code==201,job.text; assert job.json()['source_enquiry_id']==enquiry_id and job.json()['number'].startswith('DMJC-') and job.json()['status']=='AWAITING_PAYMENT' and job.json()['remarks']=='Await initial deposit'
        package_milestones=client.get(f"/api/job-cards/{job.json()['id']}/milestones",headers=ah);assert package_milestones.status_code==200 and [m['code'] for m in package_milestones.json()]==['M1','M2']
        linked=client.get(f'/api/enquiries/{enquiry_id}/related-records',headers=kh); assert linked.status_code==200 and linked.json()['job_cards'][0]['id']==job.json()['id'] and len(linked.json()['tickets'])==2 and all(row['job_card_id']==job.json()['id'] for row in linked.json()['tickets'])
        follow_relationships=client.get(f"/api/tickets/{follow_ticket.json()['id']}/relationships",headers=kh).json();assert any(r['related_job_card_id']==job.json()['id'] for r in follow_relationships)
        duplicate_job=client.post('/api/job-cards',headers=ah,json=job_data); assert duplicate_job.status_code==409
        payflow=client.put('/api/workflow-templates/PROJECT_START',headers=ah,json={'code':'PROJECT_START','name':'Project activation tasks','active':True,'trigger_event':'PROJECT_ACTIVATED','steps':[{'action_type':'PROJECT_KICKOFF','title':'Schedule project kickoff','department_code':'MARKETING','role':'MARKETING','due_days':2}]});assert payflow.status_code==200,payflow.text
        invoice=client.post(f"/api/job-cards/{job.json()['id']}/payments",headers=kh,json={'description':'Corrected project invoice','amount_due':'750'});assert invoice.status_code==201
        cancelled=client.post(f"/api/payments/{invoice.json()['id']}/cancel",headers=kh,json={'reason':'Invoice total entered incorrectly'});assert cancelled.status_code==200 and cancelled.json()['status']=='CANCELLED'
        reissued=client.post(f"/api/job-cards/{job.json()['id']}/payments",headers=kh,json={'description':'Corrected project invoice','amount_due':'750'});assert reissued.status_code==201 and reissued.json()['invoice_number']!=invoice.json()['invoice_number']
        paid=client.post(f"/api/payments/{reissued.json()['id']}/confirm",headers=kh,json={'amount':'750','external_reference':'CRM-PROJECT-ACTIVATION'});assert paid.status_code==200 and paid.json()['status']=='PAID'
        assert any(a['template_code']=='PROJECT_START' and a['job_card_id']==job.json()['id'] for a in client.get('/api/actions',headers=kh).json())
        milestone_flow=client.put('/api/workflow-templates/MILESTONE_WORK',headers=ah,json={'code':'MILESTONE_WORK','name':'Milestone work kickoff','active':True,'trigger_event':'MILESTONE_PAID','steps':[{'action_type':'MILESTONE_KICKOFF','title':'Start milestone work','department_code':'CONSULTING','role':'END_USER','due_days':2}]});assert milestone_flow.status_code==200,milestone_flow.text
        milestone_invoice=client.post(f"/api/job-cards/{job.json()['id']}/payments",headers=kh,json={'description':'M1 package payment','amount_due':'1500','milestone_id':package_milestones.json()[0]['id']});assert milestone_invoice.status_code==201,milestone_invoice.text
        milestone_receipt=client.post(f"/api/payments/{milestone_invoice.json()['id']}/confirm",headers=kh,json={'amount':'1500','external_reference':'CRM-M1-PAYMENT'});assert milestone_receipt.status_code==200
        assert client.get(f"/api/job-cards/{job.json()['id']}/milestones",headers=kh).json()[0]['status']!='COMPLETED'
        latest_marketing_dash=client.get('/api/dashboard/marketing',headers=kh);assert latest_marketing_dash.status_code==200 and any(r['reference']=='CRM-M1-PAYMENT' for r in latest_marketing_dash.json()['recent_payment_activity'])
        assert any(a['template_code']=='MILESTONE_WORK' and a['milestone_id']==package_milestones.json()[0]['id'] for a in client.get('/api/actions',headers=kh).json())
        finance=client.get('/api/reports/payments',headers=ah);assert finance.status_code==200 and any(r['Invoice']==reissued.json()['invoice_number'] and r['Status']=='PAID' for r in finance.json()['items'])
        dashboard=client.get('/api/dashboard/admin',headers=ah);assert dashboard.status_code==200 and dashboard.json()['kpis']['total_enquiries']>=1 and dashboard.json()['kpis']['converted_enquiries']>=1

    def test_consultant_planner_booking_conflicts_visibility_and_changes():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin};ch={'Authorization':'Bearer '+consultant};mh={'Authorization':'Bearer '+management}
        cards=client.get('/api/job-cards',headers=ch).json();assert cards
        card=cards[0]; milestones=client.get(f"/api/job-cards/{card['id']}/milestones",headers=ch).json()
        if not milestones:
            milestone=client.post(f"/api/job-cards/{card['id']}/milestones",headers=ah,json={'code':'M1','name':'Planner acceptance','planned_visits':2})
            assert milestone.status_code==201;milestones=[milestone.json()]
        planned_date=card['project_start_date']
        first=client.post('/api/planned-visits',headers=ch,json={'job_card_id':card['id'],'milestone_id':milestones[0]['id'],'visit_date':planned_date,'start_time':'10:00','expected_duration_minutes':120,'location':'Pune plant','objective':'Review production workflow'})
        assert first.status_code==201,first.text
        conflict=client.post('/api/planned-visits',headers=ch,json={'job_card_id':card['id'],'milestone_id':milestones[0]['id'],'visit_date':planned_date,'start_time':'11:00','expected_duration_minutes':60,'location':'Pune plant','objective':'Second overlapping booking'})
        assert conflict.status_code==409
        override=client.post('/api/planned-visits',headers=ch,json={'job_card_id':card['id'],'milestone_id':milestones[0]['id'],'visit_date':planned_date,'start_time':'11:00','expected_duration_minutes':60,'location':'Pune plant','objective':'Confirmed despite conflict','allow_conflict':True})
        assert override.status_code==201
        assert client.get('/api/planned-visits',headers=ah).status_code==200
        assert client.get('/api/planned-visits',headers=mh).status_code==200
        assert client.get('/api/planned-visits',headers={'Authorization':'Bearer '+client.post('/api/auth/login',json={'email':'marketing@dmastru.local','password':'Marketing@123'}).json()['access_token']}).status_code==403
        wrong_card=next((row for row in client.get('/api/job-cards',headers=ah).json() if row['id']!=card['id']),None)
        if wrong_card:
            denied=client.post('/api/planned-visits',headers=ch,json={'job_card_id':wrong_card['id'],'milestone_id':milestones[0]['id'],'visit_date':planned_date,'start_time':'14:00','expected_duration_minutes':30,'location':'Pune plant','objective':'Unauthorized booking'})
            assert denied.status_code in (403,422)
        changed=client.patch(f"/api/planned-visits/{first.json()['id']}",headers=ch,json={'visit_date':planned_date,'start_time':'15:00','reason':'Client changed the preferred time'})
        assert changed.status_code==200 and changed.json()['change_reason']=='Client changed the preferred time'
        cancelled=client.patch(f"/api/planned-visits/{first.json()['id']}",headers=ch,json={'status':'CANCELLED','reason':'Client cancelled the site visit'})
        assert cancelled.status_code==200 and cancelled.json()['status']=='CANCELLED'
        filtered=client.get(f"/api/planned-visits?job_card_id={card['id']}&start_date={planned_date}&end_date={planned_date}",headers=mh)
        assert filtered.status_code==200 and len(filtered.json())==2
