"""Tests for Prometheus-compatible metrics module."""

import pytest
from fast_api.app.core.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    REGISTRY,
    _fmt,
    track_llm_call,
)


class TestMetricLabelFormatting:
    def test_empty_labels(self):
        assert _fmt([], ()) == ""

    def test_single_label(self):
        assert _fmt(["model"], ("gpt-4",)) == '{model="gpt-4"}'

    def test_multiple_labels(self):
        result = _fmt(["model", "status"], ("gpt-4", "success"))
        assert result == '{model="gpt-4",status="success"}'


class TestCounter:
    def test_basic_inc_and_collect(self):
        c = Counter(name="test_counter", description="Test counter")
        c.inc()
        c.inc(2)
        lines = c.collect()
        assert any("test_counter 3" in line for line in lines)
        assert '# HELP test_counter Test counter' in lines
        assert '# TYPE test_counter counter' in lines

    def test_inc_with_labels(self):
        c = Counter(name="test_counter", description="Test", labelnames=["endpoint"])
        c.inc(endpoint="/health")
        c.inc(3, endpoint="/health")
        c.inc(5, endpoint="/metrics")
        lines = c.collect()
        assert any('endpoint="/health"' in line and " 4" in line for line in lines)
        assert any('endpoint="/metrics"' in line and " 5" in line for line in lines)

    def test_counter_is_monotonic(self):
        c = Counter(name="test_counter", description="Test")
        c.inc(10)
        c.inc(5)
        c.inc()
        lines = c.collect()
        assert any("test_counter 16" in line for line in lines)

    def test_missing_label_defaults_to_empty(self):
        c = Counter(name="test", description="d", labelnames=["a", "b"])
        c.inc(a="x")
        lines = c.collect()
        assert 'a="x",b=""' in "\n".join(lines)

    def test_thread_safety_does_not_corrupt(self):
        import threading
        import random

        c = Counter(name="test_thread", description="Thread safety test")
        errors = []

        def worker():
            try:
                for _ in range(100):
                    c.inc(random.random())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        lines = c.collect()
        assert len(lines) >= 3  # HELP, TYPE, at least one metric line


class TestGauge:
    def test_set_and_get(self):
        g = Gauge(name="test_gauge", description="Test gauge")
        g.set(42.5)
        lines = g.collect()
        assert any("test_gauge 42.5" in line for line in lines)

    def test_inc_dec(self):
        g = Gauge(name="test_gauge", description="Test gauge")
        g.set(10)
        g.inc(5)
        g.dec(3)
        lines = g.collect()
        assert any("test_gauge 12" in line for line in lines)

    def test_set_with_labels(self):
        g = Gauge(name="test_gauge", description="Test", labelnames=["host"])
        g.set(100, host="server1")
        g.set(200, host="server2")
        lines = g.collect()
        assert any('host="server1"' in line and " 100" in line for line in lines)
        assert any('host="server2"' in line and " 200" in line for line in lines)

    def test_type_line_is_gauge(self):
        g = Gauge(name="test_gauge", description="Test")
        lines = g.collect()
        assert '# TYPE test_gauge gauge' in lines


class TestHistogram:
    def test_basic_observe(self):
        h = Histogram(name="test_hist", description="Test histogram")
        h.observe(0.5)
        h.observe(2.0)
        h.observe(10.0)
        lines = h.collect()
        # Should have _bucket lines for each bound plus +Inf
        bucket_lines = [l for l in lines if "_bucket" in l]
        assert len(bucket_lines) == len(h.buckets) + 1  # +1 for +Inf
        assert any("test_hist_sum" in l for l in lines)
        assert any("test_hist_count 3" in l for l in lines)

    def test_histogram_bucket_counts(self):
        h = Histogram(name="test_hist", description="Test")
        # All 3 values <= 0.1
        h.observe(0.05)
        h.observe(0.08)
        h.observe(0.1)
        lines = h.collect()
        # le=0.1 bucket should be 3
        assert any('le="0.1" 3' in l for l in lines)
        # le=+Inf should be 3
        assert any('le="+Inf" 3' in l for l in lines)

    def test_histogram_with_labels(self):
        h = Histogram(name="test_hist", description="Test", labelnames=["method"])
        h.observe(1.0, method="GET")
        h.observe(5.0, method="GET")
        h.observe(0.5, method="POST")
        lines = h.collect()
        assert any('method="GET"' in l and "test_hist_count" in l and " 2" in l for l in lines)
        assert any('method="POST"' in l and "test_hist_count" in l and " 1" in l for l in lines)

    def test_histogram_sums_are_correct(self):
        h = Histogram(name="test_hist", description="Test")
        h.observe(1.0)
        h.observe(2.0)
        h.observe(3.0)
        lines = h.collect()
        assert any("test_hist_sum 6" in l for l in lines or "test_hist_sum 6.0" in l for l in lines)

    def test_custom_buckets(self):
        h = Histogram(name="test", description="d", buckets=[1.0, 5.0, 10.0])
        h.observe(3.0)
        lines = h.collect()
        assert any('le="1.0"' in l for l in lines)
        assert any('le="5.0"' in l for l in lines)

    def test_observe_above_all_buckets(self):
        h = Histogram(name="test", description="d", buckets=[1.0, 5.0])
        h.observe(100.0)
        lines = h.collect()
        # le=1.0 bucket: 0, le=5.0 bucket: 0, le=+Inf: 1
        assert any('le="1.0" 0' in l for l in lines)
        assert any('le="5.0" 0' in l for l in lines)
        assert any('le="+Inf" 1' in l for l in lines)


class TestMetricsRegistry:
    def test_register_and_generate(self):
        registry = MetricsRegistry()
        registry.counter("app_starts", "Application starts")
        registry.gauge("memory_bytes", "Memory usage")
        result = registry.generate_latest()
        assert "app_starts" in result
        assert "memory_bytes" in result

    def test_counter_factory(self):
        registry = MetricsRegistry()
        c = registry.counter("my_counter", "My counter", ["label1"])
        assert c.name == "my_counter"
        assert c.labelnames == ["label1"]

    def test_gauge_factory(self):
        registry = MetricsRegistry()
        g = registry.gauge("my_gauge", "My gauge")
        assert g.name == "my_gauge"

    def test_histogram_factory(self):
        registry = MetricsRegistry()
        h = registry.histogram("my_hist", "My hist", buckets=[0.5, 1.0])
        assert h.name == "my_hist"
        assert h.buckets == [0.5, 1.0]

    def test_duplicate_name_overwrites(self):
        registry = MetricsRegistry()
        c1 = registry.counter("dup", "First")
        c2 = registry.counter("dup", "Second")
        c1.inc()
        c2.inc()
        result = registry.generate_latest()
        # Only one metric line for "dup" — second registration overwrote first
        assert result.count("dup ") == 1

    def test_generate_latest_is_prometheus_format(self):
        registry = MetricsRegistry()
        c = registry.counter("http_requests_total", "Total HTTP requests", ["method"])
        c.inc(method="GET")
        result = registry.generate_latest()
        assert result.endswith("\n")
        assert '# HELP http_requests_total Total HTTP requests' in result
        assert '# TYPE http_requests_total counter' in result

    def test_registry_thread_safety(self):
        import threading

        registry = MetricsRegistry()
        c = registry.counter("shared", "Shared counter")
        errors = []

        def worker():
            try:
                for _ in range(100):
                    c.inc()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        result = registry.generate_latest()
        assert "shared 1000" in result


class TestPredefinedMetrics:
    def test_registry_exists(self):
        assert REGISTRY is not None

    def test_llm_requests_counter(self):
        from fast_api.app.core.metrics import llm_requests_total
        assert llm_requests_total.name == "fitness_llm_requests_total"

    def test_llm_latency_histogram(self):
        from fast_api.app.core.metrics import llm_request_latency_seconds
        assert llm_request_latency_seconds.name == "fitness_llm_request_latency_seconds"
        assert len(llm_request_latency_seconds.buckets) > 0

    def test_cache_metrics(self):
        from fast_api.app.core.metrics import cache_hits_total, cache_misses_total, cache_size
        assert cache_hits_total.name == "fitness_cache_hits_total"
        assert cache_misses_total.name == "fitness_cache_misses_total"
        assert cache_size.name == "fitness_cache_entries"

    def test_guardrail_metrics(self):
        from fast_api.app.core.metrics import guardrail_triggers_total
        assert guardrail_triggers_total.name == "fitness_guardrail_triggers_total"
        assert "severity" in guardrail_triggers_total.labelnames

    def test_api_metrics(self):
        from fast_api.app.core.metrics import api_requests_total, api_request_latency_seconds
        assert api_requests_total.name == "fitness_api_requests_total"
        assert api_request_latency_seconds.name == "fitness_api_request_latency_seconds"

    def test_agent_metrics(self):
        from fast_api.app.core.metrics import agent_runs_total, agent_run_latency_seconds
        assert agent_runs_total.name == "fitness_agent_runs_total"
        assert agent_run_latency_seconds.name == "fitness_agent_run_latency_seconds"

    def test_business_metrics(self):
        from fast_api.app.core.metrics import plans_generated_total, checkins_recorded_total
        assert plans_generated_total.name == "fitness_plans_generated_total"
        assert checkins_recorded_total.name == "fitness_checkins_recorded_total"

    def test_metrics_export_has_all_help_lines(self):
        result = REGISTRY.generate_latest()
        assert "# HELP " in result
        assert "# TYPE " in result
        # Should have at least 14 HELP lines for 14 metrics
        help_count = result.count("# HELP ")
        assert help_count >= 14


class TestTrackLLMCall:
    def test_success_tracks_metrics(self):
        tracker = track_llm_call(model="gpt-4")
        tracker.success(tokens_in=100, tokens_out=50)
        result = REGISTRY.generate_latest()
        assert 'model="gpt-4",status="success"' in result
        assert 'model="gpt-4",direction="input"' in result

    def test_failure_tracks_error(self):
        tracker = track_llm_call(model="gpt-4")
        tracker.failure()
        result = REGISTRY.generate_latest()
        assert 'status="error"' in result
        assert "fitness_errors_total" in result

    def test_success_without_tokens(self):
        tracker = track_llm_call(model="unknown-model")
        tracker.success()
        result = REGISTRY.generate_latest()
        assert 'model="unknown-model",status="success"' in result

    def test_default_model_is_unknown(self):
        tracker = track_llm_call()
        tracker.success()
        result = REGISTRY.generate_latest()
        assert 'model="unknown"' in result

    def test_latency_is_recorded(self):
        import time
        tracker = track_llm_call(model="test-model")
        time.sleep(0.02)
        tracker.success()
        result = REGISTRY.generate_latest()
        assert "fitness_llm_request_latency_seconds" in result
        # sum should be > 0
        assert any(
            "fitness_llm_request_latency_seconds_sum" in line
            and not line.strip().endswith(" 0")
            and not line.strip().endswith(" 0.0")
            for line in result.split("\n")
        )

    def test_multiple_calls_aggregate(self):
        for _ in range(3):
            tracker = track_llm_call(model="gpt-4")
            tracker.success()
        result = REGISTRY.generate_latest()
        parts = result.split('\n')
        count_line = [l for l in parts if 'fitness_llm_request_latency_seconds_count' in l and 'model="gpt-4"' in l]
        assert len(count_line) == 1
        # Count should be at least 3 (plus any from previous tests)
        count_val = int(count_line[0].strip().split()[-1])
        assert count_val >= 3
