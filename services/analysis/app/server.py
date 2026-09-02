from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .config import Settings
from .engine import AnalysisEngine
from .repository import CsvRepository


class AnalysisRequestHandler(BaseHTTPRequestHandler):
    engine: AnalysisEngine

    def log_message(self, format: str, *args: object) -> None:
        print(f"analysis-service: {format % args}")

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        try:
            if parts == ["health"]:
                self._json(
                    200,
                    {
                        "status": "ok",
                        "service": "analysis-service",
                        "windows": len(self.engine.repository.windows),
                    },
                )
                return

            if parts == ["v1", "windows"]:
                self._json(200, self.engine.list_windows())
                return

            if len(parts) == 4 and parts[:2] == ["v1", "windows"]:
                window_id, action = parts[2], parts[3]
                if action == "quality":
                    result = self.engine.quality(window_id)
                elif action == "context":
                    result = self.engine.context(window_id)
                elif action == "neighbors":
                    query = parse_qs(parsed.query)
                    radius = int(query.get("radius", ["1"])[0])
                    result = self.engine.neighbors(window_id, radius)
                elif action == "evidence":
                    result = self.engine.evidence(window_id)
                else:
                    self._json(404, {"error": f"Unknown action: {action}"})
                    return
                self._json(200, result)
                return

            self._json(404, {"error": "Route not found"})
        except (KeyError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # pragma: no cover - server boundary
            self._json(500, {"error": f"Internal analysis error: {exc}"})


def create_server(settings: Settings) -> ThreadingHTTPServer:
    repository = CsvRepository(
        settings.window_features_csv, settings.synchronized_timeseries_csv
    )
    engine = AnalysisEngine(repository, settings.fbg_validity_threshold)
    handler = type(
        "ConfiguredAnalysisHandler",
        (AnalysisRequestHandler,),
        {"engine": engine},
    )
    return ThreadingHTTPServer((settings.host, settings.port), handler)


def main() -> None:
    settings = Settings.from_env()
    server = create_server(settings)
    print(f"analysis-service listening on {settings.host}:{settings.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
