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
        cb.record_failure(FailureClass.ELEMENT_NOT_FOUND)
        cb.record_failure(FailureClass.ELEMENT_NOT_FOUND)
        assert cb.should_stop() is False

    def test_same_class_threshold_trips(self) -> None:
        cb = CircuitBreaker(max_same_class=3)
        for _ in range(3):
            cb.record_failure(FailureClass.ELEMENT_NOT_FOUND)
        assert cb.should_stop() is True

    def test_total_threshold_trips(self) -> None:
        cb = CircuitBreaker(max_same_class=100, max_total_failures=5)
        classes = [
            FailureClass.ELEMENT_NOT_FOUND,
            FailureClass.NETWORK_TIMEOUT,
            FailureClass.STUCK,
            FailureClass.AUTH_REQUIRED,
            FailureClass.STALE_ELEMENT,
        ]
        for fc in classes:
            cb.record_failure(fc)
        assert cb.should_stop() is True

    def test_total_below_threshold(self) -> None:
        cb = CircuitBreaker(max_same_class=100, max_total_failures=10)
        for fc in [FailureClass.ELEMENT_NOT_FOUND, FailureClass.STUCK]:
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
        cb.record_failure(FailureClass.STUCK)
        assert cb.dominant_failure() == "stuck"

    def test_most_frequent_wins(self) -> None:
        cb = CircuitBreaker(max_same_class=100, max_total_failures=100)
        cb.record_failure(FailureClass.ELEMENT_NOT_FOUND)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.NETWORK_TIMEOUT)
        cb.record_failure(FailureClass.STUCK)
        assert cb.dominant_failure() == "network_timeout"


class TestReset:
    def test_reset_clears_state(self) -> None:
        cb = CircuitBreaker(max_same_class=2)
        cb.record_failure(FailureClass.STUCK)
        cb.record_failure(FailureClass.STUCK)
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
