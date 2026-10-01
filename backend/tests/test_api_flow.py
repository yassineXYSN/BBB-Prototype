def test_login_invalid(client):
    resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert resp.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_with_token(client, auth_headers):
    resp = client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_full_interview_lifecycle(client, auth_headers):
    # 1. schedule
    payload = {
        "title": "Backend Engineer Screen",
        "scheduled_start": "2030-05-20T14:00:00",
        "duration_minutes": 45,
        "timezone": "Europe/Berlin",
        "candidate_name": "Cara Candidate",
        "candidate_email": "cara@example.com",
        "send_email": False,
    }
    resp = client.post("/api/v1/interviews", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    detail = resp.json()
    assert detail["status"] == "scheduled"
    assert detail["meeting_id"].startswith("interview-")
    assert len(detail["tokens"]) == 2
    candidate_link = next(t["link"] for t in detail["tokens"] if t["role"] == "candidate")
    public_id = detail["public_id"]
    token = candidate_link.rsplit("/join/", 1)[1]

    # 2. list + stats
    listing = client.get("/api/v1/interviews", headers=auth_headers).json()
    assert any(i["public_id"] == public_id for i in listing)
    stats = client.get("/api/v1/interviews/stats", headers=auth_headers).json()
    assert stats.get("scheduled", 0) >= 1

    # 3. candidate previews the invite (scheduled -> waiting)
    preview = client.get(f"/api/v1/public/join/{token}/preview")
    assert preview.status_code == 200
    assert preview.json()["candidate_name"] == "Cara Candidate"
    assert preview.json()["status"] == "waiting"
    assert preview.json()["role"] == "candidate"

    # 4. candidate joins -> live
    joined = client.post(f"/api/v1/public/join/{token}")
    assert joined.status_code == 200, joined.text
    assert joined.json()["role"] == "VIEWER"
    assert "meetingID=" in joined.json()["join_url"]

    detail = client.get(f"/api/v1/interviews/{public_id}", headers=auth_headers).json()
    assert detail["status"] == "live"
    events = [a["event"] for a in detail["audit_logs"]]
    assert "scheduled" in events and "candidate:joined" in events

    # 5. cancel is not allowed once live? (allowed -> cancelled per state machine)
    # instead test moderator join
    mod = client.post(f"/api/v1/interviews/{public_id}/moderator-join", headers=auth_headers)
    assert mod.status_code == 200
    assert mod.json()["role"] == "MODERATOR"

    # 6. unknown token
    assert client.get("/api/v1/public/join/nope/preview").status_code == 404


def test_interviewer_link_works_on_shared_join_flow(client, auth_headers):
    """The interviewer invite link must work on /public/join (preview + join).

    Before the role-aware fix, the interviewer token hit the candidate-only
    endpoint and got a 403 "This link cannot be used for this action".
    """
    payload = {
        "title": "Interviewer link flow",
        "scheduled_start": "2030-09-01T09:00:00",
        "candidate_name": "Ivy",
        "candidate_email": "ivy@example.com",
        "send_email": False,
    }
    created = client.post("/api/v1/interviews", json=payload, headers=auth_headers)
    assert created.status_code == 201, created.text
    created = created.json()
    interviewer_token = next(t for t in created["tokens"] if t["role"] == "interviewer")[
        "link"
    ].rsplit("/join/", 1)[1]

    # preview works and reports the real role, without flipping status to waiting
    prev = client.get(f"/api/v1/public/join/{interviewer_token}/preview")
    assert prev.status_code == 200, prev.text
    assert prev.json()["role"] == "interviewer"
    assert prev.json()["status"] == "scheduled"

    # unified join enters a moderator
    joined = client.post(f"/api/v1/public/join/{interviewer_token}")
    assert joined.status_code == 200, joined.text
    assert joined.json()["role"] == "MODERATOR"

    # a moderator opening the room does not mark the interview live
    detail = client.get(f"/api/v1/interviews/{created['public_id']}", headers=auth_headers).json()
    assert detail["status"] == "scheduled"

    # legacy endpoint still works with the interviewer token
    legacy = client.post(f"/api/v1/public/join-interviewer/{interviewer_token}")
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["role"] == "MODERATOR"

    # and the candidate token still enters as viewer
    candidate_token = next(t for t in created["tokens"] if t["role"] == "candidate")["link"].rsplit(
        "/join/", 1
    )[1]
    viewer = client.post(f"/api/v1/public/join/{candidate_token}")
    assert viewer.status_code == 200, viewer.text
    assert viewer.json()["role"] == "VIEWER"


def test_reschedule_and_cancel(client, auth_headers):
    payload = {
        "title": "Design Review",
        "scheduled_start": "2030-06-01T10:00:00",
        "candidate_name": "Dan",
        "candidate_email": "dan@example.com",
        "send_email": False,
    }
    created = client.post("/api/v1/interviews", json=payload, headers=auth_headers).json()
    public_id = created["public_id"]

    resp = client.post(
        f"/api/v1/interviews/{public_id}/reschedule",
        json={"scheduled_start": "2030-06-02T10:00:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["scheduled_start"].startswith("2030-06-02")

    resp = client.post(f"/api/v1/interviews/{public_id}/cancel", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # joining a cancelled interview is rejected
    token = next(t for t in resp.json()["tokens"] if t["role"] == "candidate")["link"]
    token = token.rsplit("/join/", 1)[1]
    assert client.post(f"/api/v1/public/join/{token}").status_code == 410


def test_templates_crud(client, auth_headers):
    resp = client.post(
        "/api/v1/templates",
        json={
            "title": "Frontend Screen",
            "duration_minutes": 30,
            "questions": ["Explain re-renders"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    template_id = resp.json()["public_id"]

    listing = client.get("/api/v1/templates", headers=auth_headers).json()
    assert any(t["public_id"] == template_id for t in listing)

    resp = client.delete(f"/api/v1/templates/{template_id}", headers=auth_headers)
    assert resp.status_code == 204


def test_transcript_404_when_missing(client, auth_headers):
    payload = {
        "title": "No transcript yet",
        "scheduled_start": "2030-07-01T10:00:00",
        "candidate_name": "Nina",
        "candidate_email": "nina@example.com",
        "send_email": False,
    }
    created = client.post("/api/v1/interviews", json=payload, headers=auth_headers).json()
    resp = client.get(f"/api/v1/interviews/{created['public_id']}/transcript", headers=auth_headers)
    assert resp.status_code == 404


def test_join_heals_meeting_missing_on_bbb(client, auth_headers, monkeypatch):
    """A meeting that vanished from BBB (expired/reset) is re-created on join."""
    from app.services.bbb.client import BbbError

    payload = {
        "title": "Healed meeting",
        "scheduled_start": "2030-08-01T10:00:00",
        "candidate_name": "Hana",
        "candidate_email": "hana@example.com",
        "send_email": False,
    }
    created = client.post("/api/v1/interviews", json=payload, headers=auth_headers).json()
    public_id = created["public_id"]
    token = next(t["link"] for t in created["tokens"] if t["role"] == "candidate").rsplit(
        "/join/", 1
    )[1]

    calls = {"create": 0, "hooks": 0}

    class FakeBbb:
        enabled = True

        def get_meeting_info(self, meeting_id):
            raise BbbError("getMeetingInfo", "FAILED", "meeting gone")

        def create_meeting(self, **kwargs):
            calls["create"] += 1
            return {
                "meeting_id": kwargs["meeting_id"],
                "internal_meeting_id": "internal-1",
                "attendee_pw": kwargs["attendee_pw"],
                "moderator_pw": kwargs["moderator_pw"],
                "message_key": "created",
            }

        def hooks_create(self, **kwargs):
            calls["hooks"] += 1
            return "hook-123"

        def join_url(self, **kwargs):
            return f"https://bbb.example/join?meetingID={kwargs['meeting_id']}"

    monkeypatch.setattr("app.services.scheduling.BbbClient", FakeBbb)
    monkeypatch.setattr("app.api.public.BbbClient", FakeBbb)

    from sqlalchemy import select as sa_select

    from app.db import SessionLocal as _SL
    from app.models import Interview as _IV

    with _SL() as _db:
        _iv = _db.scalar(sa_select(_IV).where(_IV.public_id == public_id))
        pw_before = _iv.join_password

    resp = client.post(f"/api/v1/public/join/{token}")
    assert resp.status_code == 200, resp.text
    assert calls["create"] == 1
    assert calls["hooks"] == 1
    assert resp.json()["join_url"].startswith("https://bbb.example/join")

    # re-provisioning must NOT rotate passwords (open browser tabs hold signed
    # URLs containing them)
    with _SL() as _db:
        _iv = _db.scalar(sa_select(_IV).where(_IV.public_id == public_id))
        assert _iv.join_password == pw_before

    detail = client.get(f"/api/v1/interviews/{public_id}", headers=auth_headers).json()
    events = [a["event"] for a in detail["audit_logs"]]
    assert "bbb:recreated" in events
    # still exactly one BbbMeeting row (interview_id is unique)
    from sqlalchemy import func, select

    from app.db import SessionLocal
    from app.models import BbbMeeting, Interview

    with SessionLocal() as db:
        interview = db.scalar(select(Interview).where(Interview.public_id == public_id))
        count = db.scalar(
            select(func.count())
            .select_from(BbbMeeting)
            .where(BbbMeeting.interview_id == interview.id)
        )
    assert count == 1
