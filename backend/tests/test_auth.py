import os
from io import BytesIO
from pathlib import Path
from zipfile import is_zipfile
Path("test_phase1.db").unlink(missing_ok=True)
os.environ.update(APP_ENV="test",DATABASE_URL="sqlite:///./test_phase1.db",JWT_SECRET="test-only-secret-value-with-32-bytes",RATE_LIMIT_MAX_REQUESTS="10000",LOGIN_RATE_LIMIT_MAX_REQUESTS="10000")
from fastapi.testclient import TestClient
from app import main
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
        msme=client.post('/api/msmes',headers=ah,json={'name':'Acme Manufacturing','msme_code':'MSME-ACME','zed_id':'ZED-100','certificate_level':'Gold'}); assert msme.status_code==201
        invalid=client.post('/api/job-cards',headers=ah,json={'number':'DMP/001','msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-09-01','project_end_date':'2026-08-31','required_md':'14','lead_consultant_id':3}); assert invalid.status_code==422
        card=client.post('/api/job-cards',headers=ah,json={'number':'DMP/001','msme_id':msme.json()['id'],'location':'Pune','project_start_date':'2026-08-01','project_end_date':'2026-10-01','required_md':'14','lead_consultant_id':consultant['user']['id'],'unit_representative_id':unit['user']['id'],'director_id':director['user']['id']}); assert card.status_code==201 and card.json()['required_hours']=='112.00'
        assert client.get('/api/job-cards',headers=ch).json()[0]['number']=='DMP/001'
        assert client.get('/api/msmes',headers=ch).status_code==403

    def test_milestones_visit_drafts_and_manday_engine():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant}
        card_id=client.get('/api/job-cards',headers=ah).json()[0]['id']
        milestone=client.post(f'/api/job-cards/{card_id}/milestones',headers=ah,json={'code':'M1','name':'Assessment','planned_visits':2}); assert milestone.status_code==201
        mid=milestone.json()['id']
        draft=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':mid,'visit_date':'2026-08-25','hours':6,'minutes':30,'work_details':'Assessment completed','rating_code':'A'}); assert draft.status_code==201 and draft.json()['status']=='DRAFT'
        assert client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json()['completed_minutes']==0
        submitted=client.post(f"/api/visits/{draft.json()['id']}/submit",headers=ch); assert submitted.status_code==200
        summary=client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json(); assert summary['completed_minutes']==390 and float(summary['completed_md'])==0.8125
        milestones=client.get(f'/api/job-cards/{card_id}/milestones',headers=ch).json(); assert milestones[0]['actual_visits']==1 and float(milestones[0]['progress'])==50
        m2=client.post(f'/api/job-cards/{card_id}/milestones',headers=ah,json={'code':'M2','name':'Implementation','planned_visits':2,'start_date':'2026-08-27','closing_date':'2026-08-29','payment_date':'2026-08-30','portal_report_submission_date':'2026-08-31'}); assert m2.status_code==201 and m2.json()['payment_date']=='2026-08-30'
        for visit_date,hours in (('2026-08-27',8),('2026-08-28',4)):
            visit=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':m2.json()['id'],'visit_date':visit_date,'hours':hours,'minutes':0,'work_details':'Precision regression visit','rating_code':'A'}); assert visit.status_code==201
            assert client.post(f"/api/visits/{visit.json()['id']}/submit",headers=ch).status_code==200
        exact=client.get(f'/api/job-cards/{card_id}/mandays',headers=ch).json(); assert exact['completed_minutes']==1110 and float(exact['completed_md'])==2.3125 and float(exact['remaining_md'])==11.6875

    def test_role_specific_dashboards_are_data_driven():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ad=client.get('/api/dashboard/admin',headers={'Authorization':'Bearer '+admin}); assert ad.status_code==200 and ad.json()['kpis']['total_users']>=4
        assert client.get('/api/dashboard/management',headers={'Authorization':'Bearer '+admin}).status_code==403
        mg=client.get('/api/dashboard/management',headers={'Authorization':'Bearer '+management}); assert mg.status_code==200 and float(mg.json()['kpis']['completed_md'])==2.3125
        user=client.get('/api/dashboard/user',headers={'Authorization':'Bearer '+consultant}); assert user.status_code==200 and user.json()['kpis']['total_projects']==1
        assert client.get('/api/dashboard/admin',headers={'Authorization':'Bearer '+consultant}).status_code==403

    def test_documents_and_sequential_approval_workflow():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']
        management=client.post('/api/auth/login',json={'email':'management@dmastru.local','password':'Manage@123'}).json()['access_token']
        consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        unit=client.post('/api/auth/login',json={'email':'unit.rep@dmastru.local','password':'UnitRep@123'}).json()['access_token']
        director=client.post('/api/auth/login',json={'email':'director@dmastru.local','password':'Director@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; mh={'Authorization':'Bearer '+management}; ch={'Authorization':'Bearer '+consultant}; uh={'Authorization':'Bearer '+unit}; dh={'Authorization':'Bearer '+director}
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
        assert client.post(f"/api/documents/{extra.json()['id']}/review",headers=mh,json={'decision':'REJECTED','comment':'Please resubmit a clearer copy'}).status_code==200
        replacement=client.post(f'/api/job-cards/{card_id}/documents',headers=ch,json={**base,'document_type':'Other Supporting Document'}); assert replacement.status_code==201 and replacement.json()['version']==2 and replacement.json()['status']=='RESUBMITTED'
        assert client.post(f"/api/job-cards/{card_id}/approvals/DIRECTOR_REVIEW",headers=mh,json={'decision':'APPROVED'}).status_code==403
        assert client.post(f"/api/job-cards/{card_id}/approvals/CONSULTANT_CONFIRMATION",headers=ch,json={'decision':'APPROVED'}).status_code==201
        assert client.post(f"/api/job-cards/{card_id}/approvals/UNIT_REPRESENTATIVE",headers=mh,json={'decision':'APPROVED'}).status_code==403
        assert client.post(f"/api/job-cards/{card_id}/approvals/UNIT_REPRESENTATIVE",headers=uh,json={'decision':'APPROVED'}).status_code==201
        final=client.post(f"/api/job-cards/{card_id}/approvals/DIRECTOR_REVIEW",headers=dh,json={'decision':'APPROVED'}); assert final.status_code==201
        assert any('completed' in note['message'].lower() for note in client.get('/api/notifications',headers=ch).json())
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
        projects=client.get('/api/reports/projects',headers=mh); assert projects.status_code==200 and projects.json()[0]['Job Card']=='DMP/001'
        consultants=client.get('/api/reports/consultants',headers=mh); assert consultants.status_code==200 and float(consultants.json()[0]['Mandays'])==3.3125
        csv=client.get('/api/reports/projects/export/csv',headers=mh); assert csv.status_code==200 and b'DMP/001' in csv.content and csv.headers['content-type'].startswith('text/csv')
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
        card=client.get('/api/job-cards?search=DMP&page=1&page_size=1',headers=ah); assert card.status_code==200 and len(card.json())==1
        card_id=card.json()[0]['id']
        updated=client.patch(f'/api/job-cards/{card_id}',headers=ah,json={'location':'Mumbai','required_md':'15'}); assert updated.status_code==200 and updated.json()['required_hours']=='120.00'
        docs=client.get(f'/api/job-cards/{card_id}/documents',headers=mh).json(); download=client.get(f"/api/documents/{docs[0]['id']}/download",headers=mh); assert download.status_code==200 and download.headers['content-disposition'].startswith('attachment;')
        visits=client.get(f'/api/job-cards/{card_id}/visits',headers=mh).json(); rejected=client.post(f"/api/visits/{visits[0]['id']}/reject",headers=mh,json={'reason':'Clarify findings'}); assert rejected.status_code==200 and rejected.json()['status']=='REJECTED'
        resubmitted=client.post(f"/api/visits/{visits[0]['id']}/resubmit",headers=ch); assert resubmitted.status_code==200 and resubmitted.json()['status']=='DRAFT'
        assert client.delete(f"/api/visits/{visits[0]['id']}",headers=ch).status_code==204
        assert not client.get(f'/api/job-cards/{card_id}/visits',headers=mh).json()[0:1] or all(v['id']!=visits[0]['id'] for v in client.get(f'/api/job-cards/{card_id}/visits',headers=mh).json())

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
        card=client.get('/api/job-cards?search=Acme&zed_id=ZED-100&certificate_level=Gold',headers=ah); assert card.status_code==200 and card.json()[0]['number']=='DMP/001'
        assert client.get('/api/job-cards?delayed=false&overrun=false',headers=ah).status_code==200

    def test_visit_attachment_upload_and_authorized_download():
        admin=client.post('/api/auth/login',json={'email':'admin@dmastru.local','password':'Admin@123'}).json()['access_token']; consultant=client.post('/api/auth/login',json={'email':'consultant@dmastru.local','password':'Consult@123'}).json()['access_token']
        ah={'Authorization':'Bearer '+admin}; ch={'Authorization':'Bearer '+consultant}; card_id=client.get('/api/job-cards',headers=ah).json()[0]['id']; milestone_id=client.get(f'/api/job-cards/{card_id}/milestones',headers=ah).json()[0]['id']
        visit=client.post(f'/api/job-cards/{card_id}/visits/draft',headers=ch,json={'milestone_id':milestone_id,'visit_date':'2026-08-29','hours':1,'minutes':0,'work_details':'Attachment validation','rating_code':'A','attachment_name':'evidence.pdf','attachment_mime_type':'application/pdf','attachment_base64':'JVBERi0xLjQKJXRlc3QK'}); assert visit.status_code==201 and visit.json()['attachment_name']=='evidence.pdf'
        attachment=client.get(f"/api/visits/{visit.json()['id']}/attachment",headers=ch); assert attachment.status_code==200 and attachment.content.startswith(b'%PDF-')
