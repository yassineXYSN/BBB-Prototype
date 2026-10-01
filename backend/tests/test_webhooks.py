import json

import jwt as pyjwt

HOOK_TOKEN = "test-hook-token"


def _schedule(client, auth_headers, title="Webhook Test"):
    resp = client.post(
        "/api/v1/interviews",
        json={
            "title": title,
            "scheduled_start": "2030-08-01T09:00:00",
            "candidate_name": "Walt",
            "candidate_email": "walt@example.com",
            "send_email": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_event(client, interview, event_id, attributes=None):
    event = {
        "data": {
            "type": "event",
            "id": event_id,
            "attributes": attributes
            or {"meeting": {"external-meeting-id": interview["meeting_id"]}},
            "event": {"ts": 1700000000000},
        }
    }
    return client.post(
        f"/api/v1/webhooks/bbb/events/{HOOK_TOKEN}",
        data={"event": json.dumps(event), "timestamp": "1700000000001"},
    )


def test_events_endpoint_rejects_bad_token(client):
    resp = client.post(
        "/api/v1/webhooks/bbb/events/wrong-token",
        data={"event": "{}"},
    )
    assert resp.status_code == 403


def test_user_joined_marks_interview_live(client, auth_headers):
    interview = _schedule(client, auth_headers)
    resp = _post_event(client, interview, "user-joined")
    assert resp.status_code == 200
    detail = client.get(f"/api/v1/interviews/{interview['public_id']}", headers=auth_headers).json()
    assert detail["status"] == "live"
    assert any(a["event"] == "webhook:user-joined" for a in detail["audit_logs"])


def test_meeting_ended_callback_with_recording_marks(client, auth_headers):
    interview = _schedule(client, auth_headers, "Ended Test")
    _post_event(client, interview, "user-joined")

    resp = client.get(
        f"/api/v1/webhooks/bbb/meeting-ended/{HOOK_TOKEN}",
        params={"meetingID": interview["meeting_id"], "recordingmarks": "true"},
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/v1/interviews/{interview['public_id']}", headers=auth_headers).json()
    assert detail["status"] == "processing"


def test_meeting_ended_without_marks_ends_interview(client, auth_headers):
    interview = _schedule(client, auth_headers, "No Marks Test")
    _post_event(client, interview, "user-joined")
    resp = client.get(
        f"/api/v1/webhooks/bbb/meeting-ended/{HOOK_TOKEN}",
        params={"meetingID": interview["meeting_id"], "recordingmarks": "false"},
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/v1/interviews/{interview['public_id']}", headers=auth_headers).json()
    assert detail["status"] == "ended"


def test_recording_ready_webhook_jwt(client, auth_headers):
    interview = _schedule(client, auth_headers, "Recording Ready Test")
    claims = {"meeting_id": interview["meeting_id"], "record_id": "rec-123"}
    token = pyjwt.encode(claims, "test-bbb-secret", algorithm="HS256")

    resp = client.post(
        f"/api/v1/webhooks/bbb/recording-ready/{HOOK_TOKEN}",
        data={"signed_parameters": token},
    )
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/v1/interviews/{interview['public_id']}", headers=auth_headers).json()
    assert detail["status"] == "processing"


def test_recording_ready_rejects_bad_jwt(client, auth_headers):
    token = pyjwt.encode({"meeting_id": "x"}, "wrong-secret", algorithm="HS256")
    resp = client.post(
        f"/api/v1/webhooks/bbb/recording-ready/{HOOK_TOKEN}",
        data={"signed_parameters": token},
    )
    assert resp.status_code == 401


def test_recording_ready_for_unknown_meeting_is_ok(client):
    token = pyjwt.encode(
        {"meeting_id": "interview-missing", "record_id": "r"}, "test-bbb-secret", algorithm="HS256"
    )
    resp = client.post(
        f"/api/v1/webhooks/bbb/recording-ready/{HOOK_TOKEN}",
        data={"signed_parameters": token},
    )
    assert resp.status_code == 200
