import hashlib

from app.services.bbb.client import BbbClient, compute_checksum


def _client() -> BbbClient:
    return BbbClient(
        base_url="http://internal-bbb:8080/bigbluebutton/",
        public_base_url="https://public.example.com/bigbluebutton/",
        secret="s3cret",
    )


def test_join_url_uses_public_base():
    url = _client().join_url(meeting_id="m1", full_name="Ada", password="pw", role="VIEWER")
    assert url.startswith("https://public.example.com/bigbluebutton/api/join?")
    assert "internal-bbb" not in url


def test_server_calls_use_internal_base():
    url = _client()._build_url("isMeetingRunning", {"meetingID": "m1"})
    assert url.startswith("http://internal-bbb:8080/bigbluebutton/api/isMeetingRunning?")
    assert "public.example.com" not in url


def test_join_url_falls_back_when_public_empty():
    client = BbbClient(base_url="http://bbb/bigbluebutton", public_base_url="", secret="x")
    url = client.join_url(meeting_id="m1", full_name="Ada", password="pw")
    assert url.startswith("http://bbb/bigbluebutton/api/join?")


def test_join_url_checksum_matches_signed_query():
    url = _client().join_url(meeting_id="m1", full_name="Ada Lovelace", password="pw")
    _, _, query = url.partition("?")
    *params, checksum = query.split("&")
    assert checksum == f"checksum={compute_checksum('join', '&'.join(params), 's3cret')}"


def test_nested_call_name_checksum():
    query = "callbackURL=http%3A%2F%2Fapi%2Fhook&meetingID=m1"
    expected = hashlib.sha1(f"hooks/create{query}s3cret".encode()).hexdigest()
    assert compute_checksum("hooks/create", query, "s3cret") == expected
