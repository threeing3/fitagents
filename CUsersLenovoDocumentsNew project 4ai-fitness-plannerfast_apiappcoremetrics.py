"""Lightweight Prometheus-compatible metrics. Zero external deps."""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Counter:
    name: str
    description: str
    labelnames: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _values: dict = field(default_factory=lambda: defaultdict(float))

    def inc(self, amount=1.0, **labels):
        with self._lock:
            key = tuple(labels.get(ln, '') for ln in self.labelnames)
            self._values[key] += amount

    def collect(self):
        lines = [f'# HELP {self.name} {self.description}', f'# TYPE {self.name} counter']
        with self._lock:
            for key, val in self._values.items():
                label_str = _fmt(self.labelnames, key)
                lines.append(f'{self.name}{label_str} {val}')
        return lines


@dataclass
class Gauge:
    name: str
    description: str
    labelnames: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _values: dict = field(default_factory=lambda: defaultdict(float))

    def set(self, value, **labels):
        with self._lock:
            key = tuple(labels.get(ln, '') for ln in self.labelnames)
            self._values[key] = value

    def inc(self, amount=1.0, **labels):
        with self._lock:
            key = tuple(labels.get(ln, '') for ln in self.labelnames)
            self._values[key] += amount

    def dec(self, amount=1.0, **labels):
        self.inc(-amount, **labels)

    def collect(self):
        lines = [f'# HELP {self.name} {self.description}', f'# TYPE {self.name} gauge']
        with self._lock:
            for key, val in self._values.items():
                label_str = _fmt(self.labelnames, key)
                lines.append(f'{self.name}{label_str} {val}')
        return lines