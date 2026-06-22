"""Locust load test for high-concurrency readiness checks.

Run:
    locust -f locustfile.py --host http://127.0.0.1:1015

Set FITNESS_AUTH_TOKEN to exercise authenticated endpoints.
Set FITNESS_USER_ID to include dashboard traffic.
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task


class FitnessCoachUser(HttpUser):
    wait_time = between(1, 4)

    def on_start(self) -> None:
        token = os.getenv("FITNESS_AUTH_TOKEN", "")
        self.user_id = os.getenv("FITNESS_USER_ID", "")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    @task(3)
    def health(self) -> None:
        self.client.get("/health", name="/health")

    @task(2)
    def list_sessions(self) -> None:
        if self.headers:
            self.client.get("/v1/chat/sessions?limit=20", headers=self.headers, name="/v1/chat/sessions")

    @task(1)
    def dashboard(self) -> None:
        if self.headers and self.user_id:
            self.client.get(
                f"/v1/users/{self.user_id}/dashboard",
                headers=self.headers,
                name="/v1/users/:user_id/dashboard",
            )

    @task(1)
    def enqueue_plan_generation(self) -> None:
        if self.headers:
            self.client.post(
                "/v1/plans/generate/async",
                json={"force": False, "plan_days": 7},
                headers=self.headers,
                name="/v1/plans/generate/async",
            )
