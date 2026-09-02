from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class AnalysisServiceError(RuntimeError):
    pass


class AnalysisClient:
    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str) -> dict[str, Any]:
        request = Request(f"{self.base_url}{path}", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AnalysisServiceError(
                f"Analysis service returned HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise AnalysisServiceError(f"Cannot reach analysis service: {exc}") from exc

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def call_tool(self, tool: str, window_id: str) -> dict[str, Any]:
        encoded = quote(window_id, safe="")
        routes = {
            "check_quality": f"/v1/windows/{encoded}/quality",
            "get_context": f"/v1/windows/{encoded}/context",
            "compare_neighbors": f"/v1/windows/{encoded}/neighbors?radius=1",
            "get_evidence": f"/v1/windows/{encoded}/evidence",
        }
        try:
            route = routes[tool]
        except KeyError as exc:
            raise ValueError(f"Unsupported analysis tool: {tool}") from exc
        return self._get(route)

