
"""
Lightweight Prometheus-compatible metrics for the AI Fitness Coach.
Zero external dependencies. Exposes /metrics in Prometheus text format.
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Counter:
    """A monotonically increasing counter."""
    name: str
    description: str
    labelnames: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _values: dict = field(default_factory=lambda: defaultdict(float))

    def inc(self, amount=1.0, **labels):
        with self._lock:
            key = tuple(labels.get(ln, "") for ln in self.labelnames)
            self._values[key] += amount

    def collect(self):
        lines = ["# HELP %s %s" % (self.name, self.description), "# TYPE %s counter" % self.name]
        with self._lock:
            for key, val in self._values.items():
                label_str = _fmt(self.labelnames, key)
                lines.append("%s%s %s" % (self.name, label_str, val))
        return lines


@dataclass
class Gauge:
    """A value that can go up and down."""
    name: str
    description: str
    labelnames: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _values: dict = field(default_factory=lambda: defaultdict(float))

    def set(self, value, **labels):
        with self._lock:
            key = tuple(labels.get(ln, "") for ln in self.labelnames)
            self._values[key] = value

    def inc(self, amount=1.0, **labels):
        with self._lock:
            key = tuple(labels.get(ln, "") for ln in self.labelnames)
            self._values[key] += amount

    def dec(self, amount=1.0, **labels):
        self.inc(-amount, **labels)

    def collect(self):
        lines = ["# HELP %s %s" % (self.name, self.description), "# TYPE %s gauge" % self.name]
        with self._lock:
            for key, val in self._values.items():
                label_str = _fmt(self.labelnames, key)
                lines.append("%s%s %s" % (self.name, label_str, val))
        return lines


@dataclass
class Histogram:
    """Tracks distribution of values with pre-defined buckets."""
    name: str
    description: str
    labelnames: list = field(default_factory=list)
    buckets: list = field(default_factory=lambda: [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0])
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _bucket_counts: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    _sums: dict = field(default_factory=lambda: defaultdict(float))
    _counts: dict = field(default_factory=lambda: defaultdict(int))

    def observe(self, value, **labels):
        with self._lock:
            key = tuple(labels.get(ln, "") for ln in self.labelnames)
            self._counts[key] += 1
            self._sums[key] += value
            for b in sorted(self.buckets):
                if value <= b:
                    self._bucket_counts[key][b] += 1

    def collect(self):
        lines = ["# HELP %s %s" % (self.name, self.description), "# TYPE %s histogram" % self.name]
        with self._lock:
            for key in self._counts:
                label_str = _fmt(self.labelnames, key)
                total = self._counts[key]
                for b in sorted(self.buckets):
                    count = self._bucket_counts[key].get(b, 0)
                    lines.append('%s_bucket%s,le="%s" %s' % (self.name, label_str, b, count))
                lines.append('%s_bucket%s,le="+Inf" %s' % (self.name, label_str, total))
                lines.append("%s_sum%s %s" % (self.name, label_str, self._sums[key]))
                lines.append("%s_count%s %s" % (self.name, label_str, total))
        return lines


def _fmt(names, values):
    if not names:
        return ""
    parts = ['%s="%s"' % (n, v) for n, v in zip(names, values)]
    return "{" + ",".join(parts) + "}"


class MetricsRegistry:
    """Collects all metrics and exposes them as Prometheus text."""

    def __init__(self):
        self._metrics = {}
        self._lock = threading.Lock()

    def register(self, metric):
        with self._lock:
            self._metrics[metric.name] = metric

    def counter(self, name, description, labelnames=None):
        m = Counter(name=name, description=description, labelnames=labelnames or [])
        self.register(m)
        return m

    def gauge(self, name, description, labelnames=None):
        m = Gauge(name=name, description=description, labelnames=labelnames or [])
        self.register(m)
        return m

    def histogram(self, name, description, labelnames=None, buckets=None):
        m = Histogram(
            name=name, description=description,
            labelnames=labelnames or [],
            buckets=buckets or [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
        )
        self.register(m)
        return m

    def generate_latest(self):
        lines = []
        with self._lock:
            for metric in self._metrics.values():
                lines.extend(metric.collect())
        return "\n".join(lines) + "\n"


# ---- Singleton ----
REGISTRY = MetricsRegistry()

# ---- Fitness Coach metrics ----

# LLM
llm_requests_total = REGISTRY.counter("fitness_llm_requests_total", "Total LLM API calls", ["model", "status"])
llm_request_latency_seconds = REGISTRY.histogram("fitness_llm_request_latency_seconds", "LLM API call latency", ["model"], [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0])
llm_tokens_total = REGISTRY.counter("fitness_llm_tokens_total", "Estimated token usage", ["model", "direction"])

# Cache
cache_hits_total = REGISTRY.counter("fitness_cache_hits_total", "Semantic cache hit count")
cache_misses_total = REGISTRY.counter("fitness_cache_misses_total", "Semantic cache miss count")
cache_size = REGISTRY.gauge("fitness_cache_entries", "Number of entries in semantic cache")

# Guardrails
guardrail_triggers_total = REGISTRY.counter("fitness_guardrail_triggers_total", "Safety guardrail triggers", ["severity", "rule_id"])

# API
api_requests_total = REGISTRY.counter("fitness_api_requests_total", "API requests by endpoint", ["endpoint", "method", "status"])
api_request_latency_seconds = REGISTRY.histogram("fitness_api_request_latency_seconds", "API request latency", ["endpoint"], [0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0])
rate_limit_rejections_total = REGISTRY.counter("fitness_rate_limit_rejections_total", "Rate-limited API requests", ["endpoint"])

# Errors
errors_total = REGISTRY.counter("fitness_errors_total", "Application errors", ["type"])

# Agent
agent_runs_total = REGISTRY.counter("fitness_agent_runs_total", "Agent run count", ["run_type", "status"])
agent_run_latency_seconds = REGISTRY.histogram("fitness_agent_run_latency_seconds", "Agent run latency", ["run_type"], [0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0])
background_tasks_total = REGISTRY.counter("fitness_background_tasks_total", "Background task count", ["task_type", "status"])
background_task_latency_seconds = REGISTRY.histogram("fitness_background_task_latency_seconds", "Background task runtime", ["task_type"], [1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0])
background_task_queue_depth = REGISTRY.gauge("fitness_background_task_queue_depth", "Queued background task count", ["status"])
db_pool_capacity = REGISTRY.gauge("fitness_db_pool_capacity", "Configured database pool capacity", ["kind"])

# Business
active_users = REGISTRY.gauge("fitness_active_users", "Currently active user count estimate")
plans_generated_total = REGISTRY.counter("fitness_plans_generated_total", "Training plans generated")
checkins_recorded_total = REGISTRY.counter("fitness_checkins_recorded_total", "Daily check-ins recorded")


# ---- Helpers ----

def track_llm_call(model="unknown"):
    """Factory for tracking an LLM API call. Use .success() or .failure() to record."""
    class _T:
        def __init__(self):
            self._start = time.perf_counter()
            self._model = model
        def success(self, tokens_in=0, tokens_out=0):
            elapsed = time.perf_counter() - self._start
            llm_requests_total.inc(model=self._model, status="success")
            llm_request_latency_seconds.observe(elapsed, model=self._model)
            if tokens_in:
                llm_tokens_total.inc(tokens_in, model=self._model, direction="input")
            if tokens_out:
                llm_tokens_total.inc(tokens_out, model=self._model, direction="output")
        def failure(self):
            llm_requests_total.inc(model=self._model, status="error")
            errors_total.inc(type="llm_call_failure")
    return _T()
