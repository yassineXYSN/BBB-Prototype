import pytest

from app.models import InterviewStatus
from app.services.state_machine import InvalidTransition, can_transition, is_terminal


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (InterviewStatus.draft, InterviewStatus.scheduled, True),
        (InterviewStatus.draft, InterviewStatus.live, False),
        (InterviewStatus.scheduled, InterviewStatus.live, True),
        (InterviewStatus.scheduled, InterviewStatus.transcribed, False),
        (InterviewStatus.live, InterviewStatus.ended, True),
        (InterviewStatus.ended, InterviewStatus.processing, True),
        (InterviewStatus.processing, InterviewStatus.transcribed, True),
        (InterviewStatus.processing, InterviewStatus.ended, True),
        (InterviewStatus.transcribed, InterviewStatus.archived, True),
        (InterviewStatus.transcribed, InterviewStatus.live, False),
        (InterviewStatus.cancelled, InterviewStatus.scheduled, False),
        (InterviewStatus.failed, InterviewStatus.processing, True),
    ],
)
def test_transitions(current, target, allowed):
    assert can_transition(current, target) is allowed


def test_same_status_is_noop_allowed():
    assert can_transition(InterviewStatus.live, InterviewStatus.live) is True


def test_terminal():
    assert is_terminal(InterviewStatus.archived)
    assert is_terminal(InterviewStatus.cancelled)
    assert is_terminal(InterviewStatus.failed)
    assert not is_terminal(InterviewStatus.transcribed)


def test_exception_message():
    with pytest.raises(InvalidTransition) as exc:
        raise InvalidTransition(InterviewStatus.cancelled, InterviewStatus.live)
    assert "cancelled -> live" in str(exc.value)
