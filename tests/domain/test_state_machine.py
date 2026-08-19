"""Tests for the Order state machine."""

import pytest

from hqbacktest.domain.enums import OrderStatus
from hqbacktest.domain.state_machine import (
    TERMINAL_STATUSES,
    IllegalStateTransition,
    is_terminal,
    validate_transition,
)

VALID_PAIRS = [
    (OrderStatus.NEW, OrderStatus.ACCEPTED),
    (OrderStatus.NEW, OrderStatus.REJECTED),
    (OrderStatus.ACCEPTED, OrderStatus.PENDING),
    (OrderStatus.ACCEPTED, OrderStatus.CANCELLED),
    (OrderStatus.ACCEPTED, OrderStatus.FILLED),
    (OrderStatus.ACCEPTED, OrderStatus.REJECTED),
    (OrderStatus.PENDING, OrderStatus.FILLED),
    (OrderStatus.PENDING, OrderStatus.CANCELLED),
    (OrderStatus.PENDING, OrderStatus.REJECTED),
    (OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED),
    (OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELLED),
]


ILLEGAL_PAIRS = [
    (OrderStatus.NEW, OrderStatus.PENDING),
    (OrderStatus.NEW, OrderStatus.FILLED),
    (OrderStatus.NEW, OrderStatus.CANCELLED),
    (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.ACCEPTED, OrderStatus.ACCEPTED),
    (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED),
    (OrderStatus.PENDING, OrderStatus.ACCEPTED),
    (OrderStatus.PENDING, OrderStatus.PENDING),
    (OrderStatus.FILLED, OrderStatus.CANCELLED),
    (OrderStatus.FILLED, OrderStatus.REJECTED),
    (OrderStatus.CANCELLED, OrderStatus.FILLED),
    (OrderStatus.REJECTED, OrderStatus.ACCEPTED),
    (OrderStatus.REJECTED, OrderStatus.FILLED),
]


@pytest.mark.parametrize("current,target", VALID_PAIRS)
def test_valid_transitions_pass(current, target):
    validate_transition(current, target)


@pytest.mark.parametrize("current,target", ILLEGAL_PAIRS)
def test_illegal_transitions_raise(current, target):
    with pytest.raises(IllegalStateTransition):
        validate_transition(current, target)


def test_terminal_statuses_have_no_outgoing_transitions():
    for status in TERMINAL_STATUSES:
        assert is_terminal(status)
        for target in OrderStatus:
            if target is status:
                continue
            with pytest.raises(IllegalStateTransition):
                validate_transition(status, target)


def test_non_terminal_statuses_are_not_terminal():
    for status in OrderStatus:
        if status in TERMINAL_STATUSES:
            continue
        assert not is_terminal(status)
