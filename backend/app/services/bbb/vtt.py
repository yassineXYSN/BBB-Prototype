"""WebVTT parsing for BBB caption/subtitle tracks."""

import re
from dataclasses import dataclass

TIMESTAMP_RE = re.compile(
    r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
SPEAKER_RE = re.compile(r"^\s*(?:<v\s+([^>]+)>)?([^<]*)(?:</v>)?(.*)$")


@dataclass
class Cue:
    idx: int
    start: float
    end: float
    speaker: str | None
    text: str


def _ts(h: str | None, m: str, s: str, ms: str) -> float:
    hours = int(h) if h else 0
    return hours * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_vtt(content: str) -> list[Cue]:
    """Parse WebVTT into ordered cues. Tolerant of missing headers and CRLF."""
    cues: list[Cue] = []
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    idx = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            match = TIMESTAMP_RE.search(line)
            if match:
                start = _ts(match.group(1), match.group(2), match.group(3), match.group(4))
                end = _ts(match.group(5), match.group(6), match.group(7), match.group(8))
                # collect text lines until blank line
                i += 1
                text_lines: list[str] = []
                while i < len(lines) and lines[i].strip():
                    text_lines.append(lines[i].strip())
                    i += 1
                speaker, text = _extract_speaker(" ".join(text_lines))
                text = _clean_text(text)
                if text:
                    cues.append(Cue(idx=idx, start=start, end=end, speaker=speaker, text=text))
                    idx += 1
                continue
        i += 1
    return cues


def _extract_speaker(raw: str) -> tuple[str | None, str]:
    match = re.match(r"<v\s+([^>]+)>(.*)", raw)
    if match:
        speaker = match.group(1).strip()
        text = re.sub(r"</v>", "", match.group(2))
        return speaker, text.strip()
    return None, raw


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)  # strip remaining tags (cues/settings)
    text = re.sub(r"\s+", " ", text).strip()
    # join hyphenated line breaks: "exam- ple" -> "example"
    text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
    return text


def cues_to_full_text(cues: list[Cue]) -> str:
    return "\n".join(cue.text for cue in cues)


def format_timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
