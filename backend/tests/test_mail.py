from app.services import mail


class _Settings:
    def __init__(self, port: int = 587):
        self.smtp_host = "smtp.example.com"
        self.smtp_port = port
        self.smtp_user = "user@example.com"
        self.smtp_password = "pw"
        self.smtp_from = "interviews@example.com"
        self.smtp_starttls = True


def _install_broken_smtp(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(mail.smtplib, "SMTP", boom)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", boom)


def test_smtp_failure_never_raises(monkeypatch):
    monkeypatch.setattr(mail, "get_settings", lambda: _Settings())
    _install_broken_smtp(monkeypatch)
    mail.send_email("to@example.com", "subject", "body")  # must not raise


def test_port_465_uses_implicit_tls(monkeypatch):
    monkeypatch.setattr(mail, "get_settings", lambda: _Settings(port=465))
    used = {}

    class FakeSSL:
        def __init__(self, host, port, timeout=None, context=None):
            used["ssl"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            used["login"] = user

        def send_message(self, msg):
            used["sent"] = msg["To"]

    def plain(*args, **kwargs):
        raise AssertionError("plain SMTP must not be used on port 465")

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSSL)
    monkeypatch.setattr(mail.smtplib, "SMTP", plain)
    mail.send_email("to@example.com", "subject", "body")
    assert used["ssl"] == ("smtp.example.com", 465)
    assert used["sent"] == "to@example.com"


def test_schedule_succeeds_even_when_smtp_is_down(client, auth_headers, monkeypatch):
    """Regression: a dead SMTP server used to 500 the schedule endpoint."""
    monkeypatch.setattr("app.services.mail.get_settings", lambda: _Settings())
    _install_broken_smtp(monkeypatch)
    resp = client.post(
        "/api/v1/interviews",
        json={
            "title": "Mail outage interview",
            "scheduled_start": "2030-09-01T10:00:00",
            "candidate_name": "Mila",
            "candidate_email": "mila@example.com",
            "send_email": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "scheduled"
