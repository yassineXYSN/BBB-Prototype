"""Recording -> transcript -> purge pipeline, exercised through the API with a fake BBB."""

SAMPLE_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
<v Interviewer>Walk me through your last project.</v>

00:00:04.000 --> 00:00:07.500
<v Candidate>I led a team of four on a pay-
ments migration.</v>
"""


class FakeBbb:
    enabled = True

    def __init__(self, tracks=None):
        self.deleted: list[str] = []
        self._tracks = (
            tracks
            if tracks is not None
            else [
                {
                    "href": "http://bbb/captions/rec-1/en.vtt",
                    "kind": "captions",
                    "label": "English",
                    "lang": "en-US",
                    "source": "live",
                }
            ]
        )
        self.recordings_meta: dict = {}

    def get_recordings(self, **kwargs):
        return [self.recordings_meta]

    def get_recording_text_tracks(self, record_id):
        return self._tracks

    def download_text_track(self, href):
        return SAMPLE_VTT

    def delete_recordings(self, record_ids):
        self.deleted.extend(record_ids)


def _schedule(client, auth_headers, title):
    resp = client.post(
        "/api/v1/interviews",
        json={
            "title": title,
            "scheduled_start": "2030-09-01T09:00:00",
            "candidate_name": "Pip",
            "candidate_email": "pip@example.com",
            "send_email": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_pipeline_transcribes_and_purges(client, auth_headers, monkeypatch):
    interview = _schedule(client, auth_headers, "Pipeline Test")
    fake = FakeBbb()
    fake.recordings_meta = {
        "record_id": "rec-1",
        "meeting_id": interview["meeting_id"],
        "name": "Pipeline Test",
        "state": "published",
        "playback": {"link": "http://bbb/playback/rec-1", "format": "video", "duration": 120},
        "meta": {"interview-id": interview["public_id"]},
    }
    monkeypatch.setattr("app.services.pipeline._client", lambda: fake)

    resp = client.post(f"/api/v1/interviews/{interview['public_id']}/process", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()

    # transcript stored
    assert detail["status"] == "transcribed"
    assert len(detail["transcripts"]) == 1
    transcript = detail["transcripts"][0]
    assert len(transcript["segments"]) == 2
    assert transcript["segments"][0]["speaker"] == "Interviewer"
    assert "payments migration" in transcript["segments"][1]["text"]
    assert "Walk me through" in transcript["full_text"]

    # raw recording deleted, only text kept
    assert fake.deleted == ["rec-1"]
    recordings = detail["recordings"]
    assert len(recordings) == 1
    assert recordings[0]["purged_at"] is not None
    assert recordings[0]["state"] == "deleted"


def test_pipeline_never_purges_without_transcript(client, auth_headers, monkeypatch):
    interview = _schedule(client, auth_headers, "No Captions Test")
    fake = FakeBbb(tracks=[])
    fake.recordings_meta = {
        "record_id": "rec-2",
        "meeting_id": interview["meeting_id"],
        "name": "No Captions",
        "state": "published",
        "playback": {"link": "http://bbb/playback/rec-2"},
        "meta": {"interview-id": interview["public_id"]},
    }
    monkeypatch.setattr("app.services.pipeline._client", lambda: fake)

    resp = client.post(f"/api/v1/interviews/{interview['public_id']}/process", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    detail = resp.json()

    # transcript missing -> no purge, not marked transcribed
    assert detail["status"] != "transcribed"
    assert detail["transcripts"] == []
    assert fake.deleted == []
    recordings = detail["recordings"]
    assert len(recordings) == 1
    assert recordings[0]["purged_at"] is None


def test_pipeline_prefers_configured_language(client, auth_headers, monkeypatch):
    interview = _schedule(client, auth_headers, "Lang Test")
    fake = FakeBbb(
        tracks=[
            {"href": "http://bbb/de.vtt", "lang": "de-DE", "source": "automatic"},
            {"href": "http://bbb/en.vtt", "lang": "en-US", "source": "live"},
        ]
    )
    fake.download_text_track = lambda href: SAMPLE_VTT
    fake.recordings_meta = {
        "record_id": "rec-3",
        "meeting_id": interview["meeting_id"],
        "name": "Lang",
        "state": "published",
        "playback": {"link": "x"},
        "meta": {"interview-id": interview["public_id"]},
    }
    monkeypatch.setattr("app.services.pipeline._client", lambda: fake)

    client.post(f"/api/v1/interviews/{interview['public_id']}/process", headers=auth_headers)
    detail = client.get(f"/api/v1/interviews/{interview['public_id']}", headers=auth_headers).json()
    assert detail["transcripts"][0]["language"] == "en-US"
