from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .analysis_client import AnalysisClient, AnalysisServiceError
from .config import Settings
from .orchestrator import AgentOrchestrator, AuditLog
from .planner import build_planner
from .publication import PublicationResultsError, load_publication_results


class AgentRequestHandler(BaseHTTPRequestHandler):
    orchestrator: AgentOrchestrator
    analysis_client: AnalysisClient
    deliverables_dir: Path

    def log_message(self, format: str, *args: object) -> None:
        print(f"agent-service: {format % args}")

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        if parsed.path in {"/", "/app", "/paper"}:
            filename = "publication.html" if parsed.path == "/paper" else "index.html"
            page = Path(__file__).with_name("static") / filename
            self._html(200, page.read_bytes())
            return
        if parsed.path == "/health":
            try:
                dependency = self.analysis_client.health()
                planner = self.orchestrator.planner.health()
                self._json(
                    200,
                    {
                        "status": "ok" if planner.get("ready") else "degraded",
                        "service": "agent-service",
                        "analysis_service": dependency.get("status"),
                        "planner": planner,
                    },
                )
            except AnalysisServiceError as exc:
                self._json(503, {"status": "degraded", "error": str(exc)})
            return
        if parsed.path == "/v1/windows":
            try:
                self._json(200, self.analysis_client.list_windows())
            except AnalysisServiceError as exc:
                self._json(502, {"error": str(exc)})
            return
        if parsed.path == "/v1/publication":
            try:
                self._json(200, load_publication_results(self.deliverables_dir))
            except PublicationResultsError as exc:
                self._json(503, {"error": str(exc)})
            return
        if (
            len(parts) == 4
            and parts[:2] == ["v1", "windows"]
            and parts[3] == "visualization"
        ):
            try:
                self._json(200, self.analysis_client.visualization(parts[2]))
            except AnalysisServiceError as exc:
                self._json(502, {"error": str(exc)})
            return
        self._json(404, {"error": "Route not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/analyze":
            self._json(404, {"error": "Route not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 64_000:
                raise ValueError("Request body must contain a small JSON object")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            window_id = str(payload.get("window_id", "")).strip()
            if not window_id:
                raise ValueError("window_id is required")
            self._json(200, self.orchestrator.analyze(window_id))
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except AnalysisServiceError as exc:
            self._json(502, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - server boundary
            self._json(500, {"error": f"Internal agent error: {exc}"})


def create_server(settings: Settings) -> ThreadingHTTPServer:
    client = AnalysisClient(settings.analysis_base_url)
    planner = build_planner(
        settings.llm_mode, settings.ollama_base_url, settings.ollama_model
    )
    orchestrator = AgentOrchestrator(
        client=client,
        planner=planner,
        audit_log=AuditLog(settings.audit_log_path),
        max_steps=settings.max_steps,
    )
    handler = type(
        "ConfiguredAgentHandler",
        (AgentRequestHandler,),
        {
            "orchestrator": orchestrator,
            "analysis_client": client,
            "deliverables_dir": settings.deliverables_dir,
        },
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)


def main() -> None:
    settings = Settings.from_env()
    server = create_server(settings)
    print(
        f"agent-service listening on {settings.host}:{settings.port} "
        f"with backend={settings.llm_mode}"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
