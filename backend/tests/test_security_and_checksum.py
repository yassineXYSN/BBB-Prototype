from app.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.services.bbb.client import compute_checksum


def test_password_hash_roundtrip():
    stored = hash_password("s3cret")
    assert verify_password("s3cret", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("s3cret", "garbage")


def test_jwt_roundtrip():
    token = create_access_token("user-123", "admin")
    payload = decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"


def test_bbb_checksum_documented_vector():
    """Checksum example straight from the official BigBlueButton API docs."""
    query = "name=Test+Meeting&meetingID=abc123&attendeePW=111222&moderatorPW=333444"
    secret = "639259d4-9dd8-4b25-bf01-95f9567eaf4b"
    assert compute_checksum("create", query, secret) == "1fcbb0c4fc1f039f73aa6d697d2db9ba7f803f17"
