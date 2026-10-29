"""Local JSON API for the Vite review UI. No auth; bind to localhost."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import PROJECT_DIR
from .database import (
    get_job_detail,
    list_jobs,
    list_review_queue,
    set_review_status,
    summarize_jobs,
)

FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"


def create_server(
    host: str,
    port: int,
    db_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    """Build a threaded HTTP server that reads the agent SQLite file."""

    return ThreadingHTTPServer((host, port), _handler_class(db_path))


def run_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    db_path: str | Path | None = None,
) -> None:
    """Serve ``/api`` (and ``frontend/dist`` when it exists) until interrupted."""

    server = create_server(host, port, db_path)
    origin = f"http://{host}:{port}"
    print(f"Local review API: {origin}", flush=True)
    print("  GET  /api/jobs", flush=True)
    print("  GET  /api/review-queue", flush=True)
    if FRONTEND_DIST.is_dir():
        print(f"Built UI:         {origin}", flush=True)
    else:
        print(
            "Vite UI:          cd frontend && npm install && npm run dev",
            flush=True,
        )
        print("Then open http://127.0.0.1:5173", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()


def _handler_class(db_path: str | Path | None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:
            self._send(204, b"", extra_headers=self._cors_headers())

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            try:
                if parts == ["api", "health"]:
                    self._send_json(200, {"ok": True})
                    return
                if parts == ["api", "stats"]:
                    self._send_json(200, summarize_jobs(path=db_path))
                    return
                if parts == ["api", "jobs"]:
                    jobs = list_jobs(
                        path=db_path,
                        query=query.get("q", ""),
                        outcome=query.get("outcome") or None,
                        apply_status=query.get("apply_status") or None,
                    )
                    self._send_json(200, {"jobs": jobs, "total": len(jobs)})
                    return
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
                    job = get_job_detail(parts[2], path=db_path)
                    if job is None:
                        self._send_json(404, {"error": "Job not found"})
                        return
                    self._send_json(200, job)
                    return
                if parts == ["api", "review-queue"]:
                    status = query.get("status") or None
                    if status == "all":
                        status = None
                    items = list_review_queue(path=db_path, status=status)
                    self._send_json(200, {"items": items, "total": len(items)})
                    return
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            if parts[:1] == ["api"]:
                self._send_json(404, {"error": "Not found"})
                return
            self._serve_frontend(parsed.path)

        def do_PATCH(self) -> None:
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            if len(parts) != 3 or parts[:2] != ["api", "review-queue"]:
                self._send_json(404, {"error": "Not found"})
                return
            try:
                item_id = int(parts[2])
            except ValueError:
                self._send_json(400, {"error": "Review id must be an integer"})
                return
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            status = payload.get("status")
            try:
                item = set_review_status(item_id, str(status or ""), path=db_path)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            if item is None:
                self._send_json(404, {"error": "Review item not found"})
                return
            self._send_json(200, item)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"api: {self.address_string()} {format % args}", flush=True)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _serve_frontend(self, url_path: str) -> None:
            if not FRONTEND_DIST.is_dir():
                self._send_json(
                    200,
                    {
                        "service": "jobsearchagent",
                        "message": (
                            "API is running. Start the UI with "
                            "`cd frontend && npm install && npm run dev` "
                            "and open http://127.0.0.1:5173"
                        ),
                        "jobs": "/api/jobs",
                        "review_queue": "/api/review-queue",
                    },
                )
                return
            relative = url_path.lstrip("/") or "index.html"
            target = (FRONTEND_DIST / relative).resolve()
            dist_root = FRONTEND_DIST.resolve()
            if not str(target).startswith(str(dist_root)):
                self._send_json(403, {"error": "Forbidden"})
                return
            if not target.is_file():
                target = dist_root / "index.html"
            data = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.name.endswith(".js"):
                content_type = "text/javascript; charset=utf-8"
            elif target.name.endswith(".css"):
                content_type = "text/css; charset=utf-8"
            elif target.name.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            self._send(
                200,
                data,
                content_type=content_type,
                extra_headers=self._cors_headers(),
            )

        def _send_json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                **self._cors_headers(),
            }
            self._send(code, body, extra_headers=headers)

        def _send(
            self,
            code: int,
            body: bytes,
            *,
            content_type: str | None = None,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(code)
            headers = dict(extra_headers or {})
            if content_type:
                headers["Content-Type"] = content_type
            headers.setdefault("Content-Length", str(len(body)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        @staticmethod
        def _cors_headers() -> dict[str, str]:
            return {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, PATCH, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }

    return Handler
