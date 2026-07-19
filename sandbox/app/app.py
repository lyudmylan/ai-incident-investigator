"""The sandbox's one container: a demo checkout service that misbehaves on
command (#81).

Stdlib only. It plays four roles at once so the whole product loop runs
live with zero external dependencies:

- /metrics                       Prometheus exposition (scraped)
- /api/0/issues/9001/...         a Sentry-like issue (the alert anchor)
- /flags/{env}/{key}             the pilot's flag API (GET + PATCH) -
                                 the INCIDENT IS THE FLAG: enabling
                                 checkout_enrichment degrades the service,
                                 and the tool's own `execute --live` PATCH
                                 is what ends it
- /incident/start | /incident/stop   curl-friendly aliases for the flag
- a background thread pushes logs to Loki's push API (no promtail needed)

Nothing here is load-bearing for the product; it exists so a laptop can
experience collect -> investigate -> approve -> execute -> verify without
touching a real environment.
"""

import json
import os
import random
import sys
import threading
import time
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
PORT = int(os.environ.get("PORT", "8000"))

_lock = threading.Lock()
_flags: dict[str, dict[str, bool]] = {"staging": {"checkout_enrichment": False}}
_order = random.randint(41000, 42000)


def _incident_active() -> bool:
    with _lock:
        return _flags["staging"]["checkout_enrichment"]


def _metrics_text() -> str:
    incident = _incident_active()
    # healthy jitter must sit INSIDE the recovery rule's +/-10%-of-baseline
    # band (docs/assumptions.md), or compare can never call the demo
    # recovered: sigma 2.5 keeps p95 in ~85-95 against a ~90 baseline, and
    # sigma 0.012 keeps errors in ~0.38-0.42 against ~0.4
    p95 = random.gauss(1900, 120) if incident else random.gauss(90, 2.5)
    err = random.gauss(7.5, 0.6) if incident else max(0.1, random.gauss(0.4, 0.012))
    rps = max(1.0, random.gauss(42, 4))
    return (
        "# TYPE p95_latency_ms gauge\n"
        f"p95_latency_ms {max(1.0, p95):.1f}\n"
        "# TYPE error_rate_pct gauge\n"
        f"error_rate_pct {err:.2f}\n"
        "# TYPE requests_per_second gauge\n"
        f"requests_per_second {rps:.1f}\n"
    )


def _issue_json() -> bytes:
    payload = {
        "id": "9001",
        "title": "Checkout failures: payment eligibility lookups timing out",
        "level": "error",
        "lastSeen": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "culprit": "checkout.eligibility",
        "project": {"slug": "checkout-service"},
        "metadata": {"value": "Eligibility lookup timed out after 2000ms"},
        "permalink": "http://localhost:8000/api/0/issues/9001/",
    }
    return json.dumps(payload).encode()


def _push_logs_forever() -> None:
    """Push one small batch to Loki every ~2s; failures are noted once and
    retried forever (Loki takes a few seconds to come up)."""
    global _order
    warned = False
    attempt = 1
    while True:
        time.sleep(2)
        incident = _incident_active()
        now_ns = str(time.time_ns())
        _order += 1
        lines: list[tuple[str, str, str]] = [
            (
                "info",
                now_ns,
                f"INFO checkout ok order={_order} latency_ms={random.randint(60, 140)}",
            )
        ]
        if incident:
            attempt = attempt % 5 + 1
            lines.append(
                (
                    "warning",
                    now_ns,
                    f"WARN Retrying eligibility lookup for order {_order} (attempt {attempt} of 5)",
                )
            )
            lines.append(
                (
                    "error",
                    now_ns,
                    f"ERROR Eligibility lookup timed out after 2000ms for order {_order} "
                    f"(attempt {attempt} of 5)",
                )
            )
            if attempt == 5:
                lines.append(
                    (
                        "error",
                        now_ns,
                        f"ERROR Checkout failed for order {_order}: eligibility lookup "
                        "exhausted retries (5 of 5)",
                    )
                )
        streams = [
            {
                "stream": {"app": "checkout-service", "level": level},
                "values": [[ts, line]],
            }
            for level, ts, line in lines
        ]
        body = json.dumps({"streams": streams}).encode()
        request = urllib.request.Request(
            f"{LOKI_URL}/loki/api/v1/push",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5).close()
            warned = False
        except Exception as exc:
            if not warned:
                print(f"loki push failing (will keep retrying): {exc}", file=sys.stderr)
                warned = True


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _flag_route(self) -> tuple[str, str] | None:
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "flags":
            return parts[1], parts[2]
        return None

    def do_GET(self) -> None:
        if self.path == "/metrics":
            self._send(200, _metrics_text().encode(), "text/plain; version=0.0.4")
        elif self.path == "/api/0/issues/9001/":
            self._send(200, _issue_json())
        elif self.path.startswith("/api/0/issues/9001/events/latest"):
            self._send(404, b'{"detail": "no event payload in the sandbox"}')
        elif (route := self._flag_route()) is not None:
            environment, key = route
            with _lock:
                if key not in _flags.get(environment, {}):
                    self._send(404, b'{"detail": "unknown flag"}')
                    return
                on = _flags[environment][key]
            self._send(200, json.dumps({"key": key, "on": on}).encode())
        else:
            self._send(404, b'{"detail": "unknown route"}')

    def do_PATCH(self) -> None:
        route = self._flag_route()
        if route is None:
            self._send(404, b'{"detail": "unknown route"}')
            return
        environment, key = route
        length = int(self.headers.get("Content-Length", "0"))
        try:
            desired = bool(json.loads(self.rfile.read(length) or b"{}").get("on"))
        except json.JSONDecodeError:
            self._send(400, b'{"detail": "body must be JSON with an \\"on\\" bool"}')
            return
        with _lock:
            if key not in _flags.get(environment, {}):
                self._send(404, b'{"detail": "unknown flag"}')
                return
            _flags[environment][key] = desired
        print(f"flag {environment}/{key} -> on={desired}", file=sys.stderr)
        self._send(200, json.dumps({"key": key, "on": desired}).encode())

    def do_POST(self) -> None:
        if self.path in ("/incident/start", "/incident/stop"):
            desired = self.path.endswith("start")
            with _lock:
                _flags["staging"]["checkout_enrichment"] = desired
            state = "STARTED (flag checkout_enrichment on)" if desired else "stopped (flag off)"
            print(f"incident {state}", file=sys.stderr)
            self._send(200, json.dumps({"incident": desired}).encode())
        else:
            self._send(404, b'{"detail": "unknown route"}')

    def log_message(self, format: str, *args: object) -> None:
        pass  # keep container output readable; state changes are logged above


def main() -> None:
    threading.Thread(target=_push_logs_forever, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"sandbox checkout-service on :{PORT} (loki: {LOKI_URL})", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
