"""Tests for workers.reliability.circuit_breaker — failure accumulation and trip logic."""

from __future__ import annotations

import pytest

from workers.shared_types import FailureClass
from workers.reliability.circuit_breaker import CircuitBreaker


class TestCircuitBreakerThresholds:
    def test_no_failures_does_not_trip(self) -> None:
        cb = CircuitBreaker()
        assert cb.should_stop() is False

    def test_below_same_class_threshold(self) -> None:
        cb = CircuitBreaker(max_same_class=3)
        cb.record_failure(FailureClass.BROWSER_ELEMENT_MISSING)
        cb.record_failure(FailureClass.BROWSER_ELEMENT_MISSING)
        assert cb.should_stop() is False

    def test_same_class_threshold_trips(self) -> None:
        cb = CircuitBreaker(max_same_class=3)
        for _ in range(3):
            cb.record_failure(FailureClass.BROWSER_ELEMENT_MISSING)
        assert cb.should_stop() is True

    def test_total_threshold_trips(self) -> None:
        cb = CircuitBreaker(max_same_class=100, max_total_failures=5)
        classes = [
            FailureClass.BROWSER_ELEMENT_MISSING,
            FailureClass.NETWORK_TIMEOUT,
            FailureClass.AGENT_LOOP,
            FailureClass.AUTH_REQUIRED,
            FailureClass.BROWSER_TIMEOUT,
        ]
        for fc in classes:
            cb.record_failure(fc)
        assert cb.should_stop() is True

    def test_total_below_threshold(self) -> None:
        cb = CircuitBreaker(max_same_class=100, max_total_failures=10)
        for fc in [FailureClass.BROWSER_ELEMENT_MISSING, FailureClass.AGENT_LOOP]:
            cb.record_failure(fc)
        assert cb.should_stop() is False

    def test_same_class_trips_before_total(self) -> None:
        cb = CircuitBreaker(max_same_class=2, max_total_failures=100)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        assert cb.should_stop() is True


class TestDominantFailure:
    def test_empty_returns_none(self) -> None:
        cb = CircuitBreaker()
        assert cb.dominant_failure() is None

    def test_single_class(self) -> None:
        cb = CircuitBreaker()
        cb.record_failure(FailureClass.AGENT_LOOP)
        assert cb.dominant_failure() == "agent_loop"

    def test_most_frequent_wins(self) -> None:
        cb = CircuitBreaker(max_same_class=100, max_total_failures=100)
        cb.record_failure(FailureClass.BROWSER_ELEMENT_MISSING)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.AGENT_LOOP)
        assert cb.dominant_failure() == "network_timeout"


class TestReset:
    def test_reset_clears_state(self) -> None:
        cb = CircuitBreaker(max_same_class=2)
        cb.record_failure(FailureClass.AGENT_LOOP)
        cb.record_failure(FailureClass.AGENT_LOOP)
        assert cb.should_stop() is True

        cb.reset()
        assert cb.should_stop() is False
        assert cb.dominant_failure() is None

    def test_reset_allows_reuse(self) -> None:
        cb = CircuitBreaker(max_same_class=3)
        for _ in range(3):
            cb.record_failure(FailureClass.AUTH_REQUIRED)
        assert cb.should_stop() is True

        cb.reset()
        cb.record_failure(FailureClass.AUTH_REQUIRED)
        assert cb.should_stop() is False
