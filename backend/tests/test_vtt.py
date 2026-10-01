from app.services.bbb.vtt import cues_to_full_text, format_timestamp, parse_vtt

SAMPLE = """WEBVTT

00:00:01.000 --> 00:00:04.500
<v Interviewer>Thanks for joining — can you tell me
about yourself?</v>

00:00:05.000 --> 00:00:09.000
<v Candidate>Sure! I have been building back-
end services for six years.</v>

00:01:02.100 --> 00:01:05.999
<00:01:03.000><c>noise</c>
Unstyled cue with timing tags.
"""


def test_parse_vtt_cues_and_speakers():
    cues = parse_vtt(SAMPLE)
    assert len(cues) == 3
    assert cues[0].start == 1.0 and cues[0].end == 4.5
    assert cues[0].speaker == "Interviewer"
    assert "joining" in cues[0].text
    # hyphenated line break is rejoined
    assert cues[1].text == "Sure! I have been building backend services for six years."
    assert cues[2].start == 62.1
    # timestamp tags stripped, styled text kept, speaker absent
    assert cues[2].speaker is None
    assert cues[2].text == "noise Unstyled cue with timing tags."


def test_full_text():
    cues = parse_vtt(SAMPLE)
    text = cues_to_full_text(cues)
    assert text.count("\n") == 2
    assert "six years." in text


def test_parse_vtt_without_header_and_crlf():
    content = "00:00:00,000 --> 00:00:01,000\r\nHello\r\n"
    cues = parse_vtt(content)
    assert len(cues) == 1
    assert cues[0].text == "Hello"


def test_format_timestamp():
    assert format_timestamp(65) == "1:05"
    assert format_timestamp(3725) == "1:02:05"
