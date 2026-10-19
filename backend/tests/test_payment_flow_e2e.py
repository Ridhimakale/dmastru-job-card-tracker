"""Full HTTP test: run with python -m unittest discover -s tests -p test_payment_flow_e2e.py -v."""
import json
import base64
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uvicorn


_TEST_DIRECTORY = tempfile.TemporaryDirectory(prefix="dmastru-enquiry-flow-")
os.environ.update(
    APP_ENV="test",
    DATABASE_URL=f"sqlite:///{(Path(_TEST_DIRECTORY.name) / 'flow.db').as_posix()}",
    JWT_SECRET="isolated-test-secret-with-more-than-32-bytes",
    RATE_LIMIT_MAX_REQUESTS="10000",
    LOGIN_RATE_LIMIT_MAX_REQUESTS="10000",
    SLA_MONITOR_INTERVAL_SECONDS="3600",
)
from app.database import engine
from app.main import app


class EnquiryOnboardingPaymentFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = cls._free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="critical")
        )
        cls.server_thread = threading.Thread(target=cls.server.run, name="payment-flow-api", daemon=True)
        cls.server_thread.start()
        for _ in range(60):
            if not cls.server_thread.is_alive():
                raise RuntimeError("The API server exited before the end-to-end test could start.")
            try:
                with urllib.request.urlopen(f"{cls.base_url}/health", timeout=1):
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.5)
        else:
            cls._stop_server()
            raise RuntimeError("The API server did not become healthy within 30 seconds.")

    @classmethod
    def tearDownClass(cls):
        cls._stop_server()
        engine.dispose()
        _TEST_DIRECTORY.cleanup()

    @classmethod
    def _free_port(cls):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @classmethod
    def _stop_server(cls):
        server = getattr(cls, "server", None)
        if server is None:
            return
        server.should_exit = True
        thread = getattr(cls, "server_thread", None)
        if thread is not None:
            thread.join(timeout=10)

    def api(self, path, method="GET", payload=None, headers=None, expected=200):
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, content = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, content = error.code, error.read()
        decoded = json.loads(content) if content else None
        self.assertEqual(status, expected, f"{method} {path} returned {status}: {decoded}")
        return decoded

    def login(self, email, password):
        result = self.api("/api/auth/login", "POST", {"email": email, "password": password})
        return {"Authorization": f"Bearer {result['access_token']}"}

    def test_user_referral_to_admin_onboarding(self):
        referrer = self.login("consultant@dmastru.local", "Consult@123")
        marketing = self.login("marketing@dmastru.local", "Marketing@123")
        management = self.login("management@dmastru.local", "Manage@123")
        admin = self.login("admin@dmastru.local", "Admin@123")

        referral = self.api(
            "/api/referrals",
            "POST",
            {
                "company_name": "E2E Referral to Onboarding",
                "contact_person": "Asha Rao",
                "email": "asha.referral@example.test",
                "phone": "9112223333",
                "industry": "Manufacturing",
                "location": "Pune",
                "requirement": "Support with ZED Silver certification readiness.",
                "deadline_date": "2026-12-15",
                "enquiry_for": ["ZED Silver"],
                "zed_id": "ZED-REFERRAL-001",
                "source": "USER_REFERRAL",
            },
            referrer,
            201,
        )
        self.assertEqual(referral["status"], "NEW")
        self.assertEqual(referral["source"], "USER_REFERRAL")
        self.assertIsNotNone(referral["assigned_marketing_user_id"])
        self.assertEqual(self.api("/api/my-referrals", headers=referrer)[0]["status"], "NEW")
        self.assertTrue(
            any(referral["number"] in item["message"] for item in self.api("/api/notifications", headers=marketing))
        )
        self.api(
            "/api/referrals",
            "POST",
            {
                "company_name": "e2e referral to onboarding",
                "contact_person": "A different contact",
                "email": "different.referral@example.test",
                "phone": "91122-23333",
                "requirement": "Another service request.",
                "deadline_date": "2026-12-15",
                "enquiry_for": ["Lean"],
                "source": "USER_REFERRAL",
            },
            referrer,
            409,
        )
        self.api(
            "/api/enquiries",
            "POST",
            {
                "company_name": "Different name but same referral email",
                "contact_person": "Asha Rao",
                "email": "asha.referral@example.test",
                "phone": "1112223333",
                "requirement": "Support with ZED Silver certification readiness.",
                "deadline_date": "2026-12-15",
                "enquiry_for": ["ZED Silver"],
                "source": "MARKETING_TEAM",
            },
            marketing,
            409,
        )
        self.assertTrue(
            self.api(f"/api/enquiries/matches?phone={referral['phone']}", headers=marketing)
        )

        marketing_queue = self.api(
            f"/api/enquiries?search={referral['number']}", headers=marketing
        )
        self.assertIn(referral["id"], {item["id"] for item in marketing_queue["items"]})
        self.api(
            f"/api/enquiries/{referral['id']}/follow-ups",
            "POST",
            {
                "follow_up_type": "CALL",
                "person_contacted": "Asha Rao",
                "notes": "Discussed the referral and requested service.",
                "next_follow_up_date": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                "next_follow_up_type": "MEETING",
                "status": "COMPLETED",
            },
            marketing,
            201,
        )
        self.api(f"/api/enquiries/{referral['id']}", "PATCH", {"status": "QUALIFIED"}, marketing)
        converted = self.api(
            f"/api/enquiries/{referral['id']}", "PATCH", {"status": "CONVERTED"}, marketing
        )
        self.assertEqual(converted["management_approval_status"], "PENDING")
        self.assertIn(
            referral["id"],
            {item["id"] for item in self.api("/api/management/enquiry-approvals", headers=management)},
        )
        self.assertNotIn(
            referral["id"],
            {item["id"] for item in self.api("/api/admin/enquiries/onboarding", headers=admin)["items"]},
        )

        self.api(
            f"/api/management/enquiries/{referral['id']}/approval",
            "POST",
            {"decision": "APPROVED", "comment": "Referral scope approved."},
            management,
        )
        self.assertIn(
            referral["id"],
            {item["id"] for item in self.api("/api/admin/enquiries/onboarding", headers=admin)["items"]},
        )
        onboarding = self.api(
            f"/api/admin/enquiries/{referral['id']}/onboarding",
            "POST",
            {
                "service_name": "ZED Silver",
                "scope": "Approved ZED Silver certification readiness and support.",
                "location": "Pune",
            },
            admin,
            201,
        )
        self.assertEqual(onboarding["status"], "ONBOARDED")
        self.assertEqual(self.api("/api/my-referrals", headers=referrer)[0]["status"], "SUCCESSFUL")

    def test_marketing_enquiry_admin_onboarding_finance_and_job_card(self):
        marketing = self.login("marketing@dmastru.local", "Marketing@123")
        admin = self.login("admin@dmastru.local", "Admin@123")
        management = self.login("management@dmastru.local", "Manage@123")
        unit_rep = self.login("unit.rep@dmastru.local", "UnitRep@123")
        finance = self.login("finance@dmastru.local", "Finance@123")
        consultant_login = self.api(
            "/api/auth/login", "POST", {"email": "consultant@dmastru.local", "password": "Consult@123"}
        )
        consultant = {"Authorization": f"Bearer {consultant_login['access_token']}"}

        next_follow_up = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        enquiry = self.api(
            "/api/enquiries",
            "POST",
            {
                "company_name": "E2E ZED Silver Manufacturing",
                "contact_person": "Asha Rao",
                "email": "asha.e2e@example.test",
                "phone": "9876501234",
                "industry": "Manufacturing",
                "location": "Pune",
                "requirement": "Support with ZED Silver certification readiness.",
                "deadline_date": "2026-12-15",
                "enquiry_for": ["ZED Silver"],
                "zed_id": "ZED-E2E-001",
                "source": "MARKETING_TEAM",
                "expected_scope": "Readiness assessment and certification support.",
                "status": "NEW",
            },
            marketing,
            201,
        )
        self.assertRegex(enquiry["number"], r"^DMENQ-\d{4}-\d{5}$")
        self.api(
            "/api/referrals",
            "POST",
            {
                "company_name": "Marketing enquiry submitted again as a referral",
                "email": "different.marketing@example.test",
                "phone": "98765-01234",
                "requirement": "Same client, formatted phone number.",
                "deadline_date": "2026-12-15",
                "enquiry_for": ["Lean"],
                "source": "USER_REFERRAL",
            },
            consultant,
            409,
        )
        editable_enquiry = self.api(
            "/api/enquiries",
            "POST",
            {
                "company_name": "Separate E2E client record",
                "email": "separate.e2e@example.test",
                "phone": "9000001111",
                "requirement": "A separate valid client request.",
                "deadline_date": "2026-12-15",
                "enquiry_for": ["Lean"],
                "source": "MARKETING_TEAM",
            },
            marketing,
            201,
        )
        self.api(
            f"/api/enquiries/{editable_enquiry['id']}",
            "PATCH",
            {"company_name": "E2E ZED Silver Manufacturing"},
            marketing,
            409,
        )
        self.api(
            f"/api/enquiries/{enquiry['id']}/follow-ups",
            "POST",
            {
                "follow_up_type": "CALL",
                "person_contacted": "Asha Rao",
                "notes": "Discussed ZED Silver scope and onboarding requirements.",
                "next_follow_up_date": next_follow_up,
                "next_follow_up_type": "MEETING",
                "status": "COMPLETED",
            },
            marketing,
            201,
        )
        self.api(f"/api/enquiries/{enquiry['id']}", "PATCH", {"status": "CONVERTED"}, marketing, 409)
        self.api(f"/api/enquiries/{enquiry['id']}", "PATCH", {"status": "QUALIFIED"}, marketing)
        converted = self.api(f"/api/enquiries/{enquiry['id']}", "PATCH", {"status": "CONVERTED"}, marketing)
        self.assertEqual(converted["status"], "CONVERTED")
        self.assertEqual(converted["management_approval_status"], "PENDING")
        self.api(f"/api/enquiries/{enquiry['id']}", "PATCH", {"feedback": "Marketing cannot edit after handoff."}, marketing, 409)

        management_queue = self.api("/api/management/enquiry-approvals", headers=management)
        self.assertIn(enquiry["id"], {row["id"] for row in management_queue})
        admin_queue = self.api("/api/admin/enquiries/onboarding", headers=admin)
        self.assertNotIn(enquiry["id"], {row["id"] for row in admin_queue["items"]})
        self.api(
            f"/api/admin/enquiries/{enquiry['id']}/onboarding",
            "POST",
            {"service_name": "ZED Silver", "scope": "Must remain blocked before Management approval.", "location": "Pune"},
            admin,
            409,
        )
        self.api(
            f"/api/management/enquiries/{enquiry['id']}/approval",
            "POST",
            {"decision": "APPROVED", "comment": "Scope and service are approved."},
            management,
            200,
        )
        admin_queue = self.api("/api/admin/enquiries/onboarding", headers=admin)
        self.assertIn(enquiry["id"], {row["id"] for row in admin_queue["items"]})
        onboarding = self.api(
            f"/api/admin/enquiries/{enquiry['id']}/onboarding",
            "POST",
            {
                "service_name": "ZED Silver",
                "scope": "Admin-confirmed ZED Silver readiness and certification scope.",
                "location": "Pune",
            },
            admin,
            201,
        )
        self.assertEqual(onboarding["status"], "ONBOARDED")
        self.assertTrue(onboarding["msme_code"].startswith("DMMSME-"))
        self.assertRegex(onboarding["project_name"], r"^DMPRJ-\d{4}-\d{5}$")
        self.assertNotIn("commercial_amount", onboarding)
        self.assertNotIn("initial_payment_amount", onboarding)
        ready_queue = self.api("/api/admin/onboardings/ready-for-job-card", headers=admin)
        ready_item = next(row for row in ready_queue["items"] if row["id"] == onboarding["id"])
        self.assertNotIn("payment", ready_item)
        self.assertNotIn("commercial_amount", ready_item)
        self.assertNotIn("initial_payment_amount", ready_item)

        card_payload = {
                "source_enquiry_id": enquiry["id"],
                "project_name": "Client input must not override onboarding",
                "service_name": "Unrequested service",
                "scope": "Client input must not override onboarding",
                "msme_id": onboarding["msme_id"],
                "location": "Pune",
                "project_start_date": "2026-11-01",
                "project_end_date": "2027-02-28",
                "required_md": "14",
                "unit_representative_id": self.api("/api/auth/me", headers=unit_rep)["id"],
            }
        self.api("/api/job-cards", "POST", card_payload, admin, 422)
        card_payload["service_name"] = "ZED Silver"
        card_payload["lead_consultant_id"] = consultant_login["user"]["id"]
        self.api("/api/job-cards", "POST", card_payload, admin, 409)
        card_payload.pop("lead_consultant_id")
        card = self.api("/api/job-cards", "POST", card_payload, admin, 201)
        self.assertRegex(card["number"], r"^DMJC-\d{4}-\d{5}$")
        self.assertEqual(card["project_name"], onboarding["project_name"])
        self.assertEqual(card["service_name"], "ZED Silver")
        self.assertEqual(card["scope"], "Admin-confirmed ZED Silver readiness and certification scope.")

        milestones = self.api(f"/api/job-cards/{card['id']}/milestones", headers=admin)
        by_code = {row["code"]: row for row in milestones}
        self.assertEqual(set(by_code), {"M1", "M2", "M3"})
        self.assertEqual([by_code[code]["planned_mandays"] for code in ("M1", "M2", "M3")], ["2.00", "6.00", "6.00"])
        self.assertTrue(all(row["amount"] is None and row["payment_status"] is None for row in milestones))
        self.api(f"/api/job-cards/{card['id']}/payments", headers=admin, expected=403)

        finance_actions = self.api("/api/actions", headers=finance)
        self.assertTrue(any(action["template_code"] == "INITIAL_MILESTONE_PAYMENT" and action["milestone_id"] == by_code["M1"]["id"] for action in finance_actions))
        self.api(
            f"/api/job-cards/{card['id']}/reassign",
            "POST",
            {"user_id": consultant_login["user"]["id"], "assignment_type": "LEAD_CONSULTANT", "reason": "Must wait for M1 payment."},
            admin,
            423,
        )
        self.api(f"/api/job-cards/{card['id']}", "PATCH", {"status": "IN_PROGRESS"}, admin, 423)
        activity_type = self.api("/api/activity-types", headers=consultant)[0]
        activity = {
            "milestone_id": by_code["M1"]["id"],
            "activity_type_id": activity_type["id"],
            "name": "Begin ZED Silver assessment",
            "description": "Consultant work remains blocked until Finance confirms M1 payment.",
        }
        activity_for_admin = {**activity, "assigned_user_id": consultant_login["user"]["id"]}
        self.api(f"/api/job-cards/{card['id']}/activities", "POST", activity_for_admin, admin, 423)
        self.api(
            f"/api/job-cards/{card['id']}/payments",
            "POST",
            {"description": "Premature M2 payment", "amount_due": "5000.00", "milestone_id": by_code["M2"]["id"]},
            finance,
            409,
        )
        m1_invoice = self.api(
            f"/api/job-cards/{card['id']}/payments",
            "POST",
            {"description": "M1 assessment payment", "amount_due": "2000.00", "milestone_id": by_code["M1"]["id"]},
            finance,
            201,
        )
        self.api(
            f"/api/payments/{m1_invoice['id']}/confirm",
            "POST",
            {"amount": "500.00"},
            finance,
            422,
        )
        m1_receipt = self.api(
            f"/api/payments/{m1_invoice['id']}/confirm",
            "POST",
            {"completed": True},
            finance,
        )
        self.assertEqual(m1_receipt["status"], "PAID")
        self.assertEqual(m1_receipt["amount_paid"], "2000.00")
        self.assertEqual(self.api(f"/api/job-cards/{card['id']}", headers=admin)["status"], "AWAITING_ASSIGNMENT")
        self.api(f"/api/job-cards/{card['id']}/activities", "POST", activity_for_admin, admin, 423)
        assigned = self.api(
            f"/api/job-cards/{card['id']}/reassign",
            "POST",
            {"user_id": consultant_login["user"]["id"], "assignment_type": "LEAD_CONSULTANT", "reason": "Assign consultant after M1 payment confirmation."},
            admin,
        )
        self.assertEqual(assigned["status"], "ASSIGNED")
        created_activity = self.api(f"/api/job-cards/{card['id']}/activities", "POST", activity, consultant, 201)
        self.api(f"/api/activities/{created_activity['id']}", "PATCH", {"status": "COMPLETED", "duration_minutes": 60}, consultant)
        finance_cards = self.api("/api/finance/job-cards", headers=finance)
        finance_card = next(row for row in finance_cards if row["id"] == card["id"])
        self.assertNotIn("initial_payment_paid", finance_card)
        admin_actions = self.api("/api/actions", headers=admin)
        self.assertTrue(any(action["template_code"] == "MILESTONE_WORK_PLANNING" and action["milestone_id"] == by_code["M1"]["id"] for action in admin_actions))

        planned_visit = self.api(
            "/api/planned-visits",
            "POST",
            {
                "job_card_id": card["id"],
                "milestone_id": by_code["M1"]["id"],
                "visit_date": "2026-11-02",
                "start_time": "10:00",
                "expected_duration_minutes": 480,
                "location": "Pune",
                "objective": "Complete the initial ZED Silver assessment.",
            },
            consultant,
            201,
        )
        duplicate_visit = self.api(
            "/api/planned-visits",
            "POST",
            {
                "job_card_id": card["id"],
                "milestone_id": by_code["M1"]["id"],
                "visit_date": "2026-11-02",
                "start_time": "10:00",
                "expected_duration_minutes": 480,
                "location": "Pune",
                "objective": "Duplicate overlapping booking regression case.",
                "allow_conflict": True,
            },
            consultant,
            201,
        )
        pdf = base64.b64encode(b"%PDF-1.4\nE2E evidence\n").decode("ascii")
        for booking in (planned_visit, duplicate_visit):
            tasks = self.api(f"/api/job-cards/{card['id']}/activities", headers=consultant)
            required_tasks = {task["name"]: task for task in tasks if task.get("planned_visit_id") == booking["id"]}
            self.assertEqual(set(required_tasks), {"Case study", "Photo", "Plan"})
            for name, task in required_tasks.items():
                self.api(
                    f"/api/activities/{task['id']}/attachments",
                    "POST",
                    {"file_name": f"{name.lower().replace(' ', '-')}.pdf", "mime_type": "application/pdf", "content_base64": pdf},
                    consultant,
                    201,
                )
                updated = self.api(f"/api/activities/{task['id']}", "PATCH", {"status": "COMPLETED"}, consultant)
                self.assertEqual(updated["status"], "COMPLETED")
            completed_visit = self.api(
                f"/api/planned-visits/{booking['id']}",
                "PATCH",
                {
                    "status": "COMPLETED",
                    "actual_date": "2026-11-02",
                    "actual_start_time": "10:00",
                    "actual_duration_minutes": 480,
                    "outcome": "Assessment completed; findings and next steps recorded.",
                },
                consultant,
            )
            self.assertEqual(completed_visit["status"], "COMPLETED")
        co_consultant = self.api(
            "/api/users",
            "POST",
            {"full_name": "E2E Co Consultant", "email": "co.consultant.e2e@example.test", "password": "E2E-CoConsultant@123", "role": "END_USER"},
            admin,
            201,
        )
        self.api(f"/api/users/{co_consultant['id']}/activate", "POST", {}, admin, 200)
        self.api(
            f"/api/job-cards/{card['id']}/reassign",
            "POST",
            {"user_id": co_consultant["id"], "assignment_type": "CO_CONSULTANT", "reason": "Verify team visit effort calculation."},
            admin,
        )
        visit_report = self.api(
            f"/api/job-cards/{card['id']}/visits/draft",
            "POST",
            {"milestone_id": by_code["M1"]["id"], "visit_date": "2026-11-02", "hours": 8, "minutes": 0, "work_details": "Recorded the site visit with both assigned consultants.", "rating_code": "A"},
            consultant,
            201,
        )
        self.assertEqual(visit_report["co_consultant_id"], co_consultant["id"])
        self.api(f"/api/visits/{visit_report['id']}/submit", "POST", {}, consultant)
        mandays = self.api(f"/api/job-cards/{card['id']}/mandays", headers=consultant)
        self.assertEqual(float(mandays["completed_md"]), 2.125, "Count 8 visit hours for each of two consultants, suppress duplicate planner hours, and include the separate 1-hour activity.")
        milestone_report = self.api(f"/api/reports/milestones?job_card_id={card['id']}", headers=management)
        m1_report = next(row for row in milestone_report["items"] if row["Milestone"] == "M1")
        self.assertEqual(m1_report["Actual MD"], "2.1250", "Milestone reports must use the same consultant-hour and planner de-duplication rules as the Job Card summary.")
        lead_report = self.api(f"/api/reports/consultants?consultant_id={consultant_login['user']['id']}", headers=management)
        co_report = self.api(f"/api/reports/consultants?consultant_id={co_consultant['id']}", headers=management)
        self.assertEqual(lead_report["items"][0]["Actual MD"], "1.1250")
        self.assertEqual(co_report["items"][0]["Actual MD"], "1.0000")
        submitted = self.api(f"/api/milestones/{by_code['M1']['id']}/submit-review", "POST", {}, consultant, 201)
        self.assertEqual(submitted["decision"], "SUBMITTED")
        self.api("/api/milestones/management-review-queue", headers=admin, expected=403)
        management_queue = self.api("/api/milestones/management-review-queue", headers=management)
        self.assertFalse(any(item["milestone_id"] == by_code["M1"]["id"] for item in management_queue), "Management must not review before Admin approves activity evidence.")
        self.api(
            f"/api/milestones/{by_code['M1']['id']}/management-review",
            "POST",
            {"decision": "APPROVED", "comment": "Must wait for Admin evidence review."},
            management,
            409,
        )
        admin_deliverables = self.api("/api/activity-attachments/review-queue", headers=admin)
        self.assertEqual(admin_deliverables["total"], 6)
        admin_activities = self.api("/api/activities/review-queue", headers=admin)
        self.assertEqual(admin_activities["total"], 7)
        for activity_item in admin_activities["items"]:
            self.api(
                f"/api/activities/{activity_item['id']}/review",
                "POST",
                {"decision": "APPROVED", "comment": "Admin reviewed and approved completed activity."},
                admin,
            )
        for deliverable in admin_deliverables["items"]:
            self.api(
                f"/api/activity-attachments/{deliverable['id']}/review",
                "POST",
                {"decision": "APPROVED", "comment": "Admin reviewed and approved evidence."},
                admin,
            )
        management_queue = self.api("/api/milestones/management-review-queue", headers=management)
        diagnostic_activities = self.api(f"/api/job-cards/{card['id']}/activities", headers=consultant)
        self.assertTrue(any(item["milestone_id"] == by_code["M1"]["id"] for item in management_queue), f"M1 still blocked after Admin approvals: {diagnostic_activities}")
        queue_item = next(item for item in management_queue if item["milestone_id"] == by_code["M1"]["id"])
        self.assertEqual(queue_item["job_card_number"], card["number"])
        self.assertEqual(len(queue_item["admin_approved_deliverables"]), 13)
        self.assertTrue(all(item["status"] == "APPROVED" for item in queue_item["admin_approved_deliverables"]))
        self.api(
            f"/api/milestones/{by_code['M1']['id']}/management-review",
            "POST",
            {"decision": "APPROVED", "comment": "Admin cannot approve Management milestones."},
            admin,
            403,
        )
        decision = self.api(
            f"/api/milestones/{by_code['M1']['id']}/management-review",
            "POST",
            {"decision": "APPROVED", "comment": "M1 reviewed in E2E acceptance flow."},
            management,
            201,
        )
        self.assertEqual((decision["stage"], decision["decision"]), ("MANAGEMENT", "APPROVED"))
        finance_job_cards = self.api("/api/finance/job-cards", headers=finance)
        finance_card = next(row for row in finance_job_cards if row["id"] == card["id"])
        finance_m1 = next(row for row in finance_card["milestones"] if row["code"] == "M1")
        finance_m2 = next(row for row in finance_card["milestones"] if row["code"] == "M2")
        self.assertEqual(finance_m1["status"], "COMPLETED")
        self.assertEqual(finance_m2["payment_status"], "PAYMENT_PENDING")
        finance_actions = self.api("/api/actions", headers=finance)
        self.assertTrue(any(action["template_code"] == "MILESTONE_NEXT_PAYMENT" and action["milestone_id"] == by_code["M2"]["id"] for action in finance_actions))
        m2_invoice = self.api(
            f"/api/job-cards/{card['id']}/payments",
            "POST",
            {"description": "M2 implementation payment", "amount_due": "6000.00", "milestone_id": by_code["M2"]["id"]},
            finance,
            201,
        )
        self.assertEqual(m2_invoice["milestone_id"], by_code["M2"]["id"])
        m2_receipt = self.api(
            f"/api/payments/{m2_invoice['id']}/confirm",
            "POST",
            {"completed": True},
            finance,
        )
        self.assertEqual(m2_receipt["status"], "PAID")
        self.assertEqual(m2_receipt["amount_paid"], "6000.00")
        self.api(f"/api/payments/{m2_invoice['id']}/confirm", "POST", {"completed": True}, finance, 409)
        final_milestones = self.api(f"/api/job-cards/{card['id']}/milestones", headers=management)
        final_m1 = next(item for item in final_milestones if item["code"] == "M1")
        self.assertEqual(final_m1["status"], "COMPLETED")
        unit_queue = self.api("/api/milestones/unit-confirmation-queue", headers=unit_rep)
        self.assertFalse(any(item["milestone_id"] == by_code["M1"]["id"] for item in unit_queue))
        self.api("/api/auth/logout", "POST", headers=finance, expected=204)
        self.api("/api/auth/me", headers=finance, expected=401)


if __name__ == "__main__":
    unittest.main()
