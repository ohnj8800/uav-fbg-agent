from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


ALLOWED_ACTIONS = {
    "check_quality",
    "get_context",
    "compare_neighbors",
    "get_evidence",
    "finalize",
}
ALLOWED_DECISIONS = {
    "STATE_CONSISTENT",
    "TRANSITION_ASSOCIATED",
    "NOT_ATTRIBUTABLE_TO_FLIGHT_STATE",
    "INSUFFICIENT_DATA",
}


class Planner(ABC):
    backend_name = "unknown"
    model_name: str | None = None

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "model": self.model_name,
            "reachable": True,
            "ready": True,
        }

    @abstractmethod
    def next_action(
        self, window_id: str, observations: dict[str, Any], tools_called: list[str]
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def finalize(
        self, window_id: str, observations: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError


class HeuristicPlanner(Planner):
    """Deterministic development backend with the same tool contract as an LLM."""

    backend_name = "heuristic"

    def next_action(
        self, window_id: str, observations: dict[str, Any], tools_called: list[str]
    ) -> str:
        if "check_quality" not in observations:
            return "check_quality"
        if not observations["check_quality"].get("sufficient", False):
            return "finalize"
        if "get_context" not in observations:
            return "get_context"
        if "compare_neighbors" not in observations:
            return "compare_neighbors"
        if "get_evidence" not in observations:
            return "get_evidence"
        return "finalize"

    def finalize(
        self, window_id: str, observations: dict[str, Any]
    ) -> dict[str, Any]:
        evidence = observations.get("get_evidence", {})
        decision = evidence.get("rule_suggestion", "STATE_CONSISTENT")
        reasons = evidence.get("rule_reasons", [])
        return {"decision": decision, "evidence": reasons}


class OllamaPlanner(Planner):
    backend_name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        fallback: Planner | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.model_name = model
        self.fallback = fallback or HeuristicPlanner()
        self.timeout_s = timeout_s
        self.last_warning: str | None = None

    def health(self) -> dict[str, Any]:
        request = Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urlopen(request, timeout=min(self.timeout_s, 5.0)) as response:
                payload = json.loads(response.read().decode("utf-8"))
            installed_models = {
                str(item.get("name") or item.get("model"))
                for item in payload.get("models", [])
                if isinstance(item, dict)
            }
            model_available = self.model in installed_models
            return {
                "backend": self.backend_name,
                "model": self.model,
                "reachable": True,
                "ready": model_available,
                "model_available": model_available,
            }
        except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return {
                "backend": self.backend_name,
                "model": self.model,
                "reachable": False,
                "ready": False,
                "model_available": False,
                "error": str(exc),
            }

    def _generate_json(self, prompt: str) -> dict[str, Any]:
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            }
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            outer = json.loads(response.read().decode("utf-8"))
        return json.loads(outer["response"])

    def next_action(
        self, window_id: str, observations: dict[str, Any], tools_called: list[str]
    ) -> str:
        prompt = (
            "You are a restricted UAV/FBG analysis planner. Select exactly one next action. "
            "Allowed actions: check_quality, get_context, compare_neighbors, get_evidence, "
            "finalize. Quality must be checked before other interpretation. Do not invent data. "
            "Return JSON only as {\"action\": \"...\", \"reason\": \"...\"}.\n"
            f"window_id={window_id}\n"
            f"tools_called={json.dumps(tools_called)}\n"
            f"observations={json.dumps(observations, ensure_ascii=False)}"
        )
        try:
            result = self._generate_json(prompt)
            action = str(result.get("action", "")).strip()
            if action not in ALLOWED_ACTIONS:
                raise ValueError(f"LLM selected disallowed action: {action!r}")
            self.last_warning = None
            return action
        except (URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self.last_warning = f"Ollama planner fallback: {exc}"
            return self.fallback.next_action(window_id, observations, tools_called)

    def finalize(
        self, window_id: str, observations: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = (
            "Interpret the supplied UAV flight context and FBG evidence. Choose exactly one "
            "decision from STATE_CONSISTENT, TRANSITION_ASSOCIATED, "
            "NOT_ATTRIBUTABLE_TO_FLIGHT_STATE, INSUFFICIENT_DATA. Never override an "
            "insufficient-data quality result. Use only supplied evidence. Return JSON only as "
            "{\"decision\": \"...\", \"evidence\": [\"...\"]}.\n"
            f"window_id={window_id}\n"
            f"observations={json.dumps(observations, ensure_ascii=False)}"
        )
        try:
            result = self._generate_json(prompt)
            decision = str(result.get("decision", "")).strip()
            evidence = result.get("evidence", [])
            if decision not in ALLOWED_DECISIONS:
                raise ValueError(f"LLM selected disallowed decision: {decision!r}")
            if not isinstance(evidence, list) or not all(
                isinstance(item, str) for item in evidence
            ):
                raise ValueError("LLM evidence must be a list of strings")
            self.last_warning = None
            return {"decision": decision, "evidence": evidence}
        except (URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self.last_warning = f"Ollama finalizer fallback: {exc}"
            return self.fallback.finalize(window_id, observations)


def build_planner(mode: str, ollama_base_url: str, ollama_model: str) -> Planner:
    if mode == "heuristic":
        return HeuristicPlanner()
    if mode == "ollama":
        return OllamaPlanner(ollama_base_url, ollama_model)
    raise ValueError("LLM_MODE must be either 'heuristic' or 'ollama'")
