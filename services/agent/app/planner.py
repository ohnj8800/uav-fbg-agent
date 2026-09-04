from __future__ import annotations

import json
import threading
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
    "NOT_ATTRIBUTABLE",
    "INSUFFICIENT_DATA",
}

PROMPT_VERSION = "uav_fbg_real_log_v1"


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

    def begin_request(self) -> None:
        """Reset request-local runtime metadata before one interpretation."""

    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "llm_attempts": 0,
            "llm_successes": 0,
            "llm_succeeded": None,
            "fallback_used": False,
            "effective_backend": self.backend_name,
            "warnings": [],
        }

    def consume_action_reason(self) -> str | None:
        """Return a concise model-supplied action rationale, never hidden chain-of-thought."""
        return None

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
        self._request_local = threading.local()

    def begin_request(self) -> None:
        self._request_local.state = {
            "attempts": 0,
            "successes": 0,
            "warnings": [],
            "action_reason": None,
        }

    def _runtime_state(self) -> dict[str, Any]:
        state = getattr(self._request_local, "state", None)
        if state is None:
            self.begin_request()
            state = self._request_local.state
        return state

    def _record_attempt(self) -> None:
        self._runtime_state()["attempts"] += 1

    def _record_success(self) -> None:
        self._runtime_state()["successes"] += 1

    def _record_failure(self, warning: str) -> None:
        self._runtime_state()["warnings"].append(warning)

    def consume_action_reason(self) -> str | None:
        state = self._runtime_state()
        reason = state.get("action_reason")
        state["action_reason"] = None
        return str(reason) if reason else None

    def runtime_metadata(self) -> dict[str, Any]:
        state = self._runtime_state()
        attempts = int(state["attempts"])
        successes = int(state["successes"])
        warnings = list(state["warnings"])
        if attempts == 0:
            effective_backend = "not_invoked"
            llm_succeeded = None
        elif successes == attempts:
            effective_backend = "ollama"
            llm_succeeded = True
        elif successes == 0:
            effective_backend = "heuristic_fallback"
            llm_succeeded = False
        else:
            effective_backend = "ollama_with_fallback"
            llm_succeeded = False
        return {
            "llm_attempts": attempts,
            "llm_successes": successes,
            "llm_succeeded": llm_succeeded,
            "fallback_used": bool(warnings),
            "effective_backend": effective_backend,
            "warnings": warnings,
        }

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
        self._record_attempt()
        try:
            result = self._generate_json(prompt)
            action = str(result.get("action", "")).strip()
            if action not in ALLOWED_ACTIONS:
                raise ValueError(f"LLM selected disallowed action: {action!r}")
            reason = str(result.get("reason", "")).strip()
            self._runtime_state()["action_reason"] = reason[:300] or None
            self._record_success()
            return action
        except (URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self._record_failure(f"Ollama planner fallback: {exc}")
            self._runtime_state()["action_reason"] = (
                "Ollama action selection failed; deterministic fallback was used"
            )
            return self.fallback.next_action(window_id, observations, tools_called)

    def finalize(
        self, window_id: str, observations: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = (
            "Interpret the supplied UAV flight context and FBG evidence. Choose exactly one "
            "decision from STATE_CONSISTENT, TRANSITION_ASSOCIATED, "
            "NOT_ATTRIBUTABLE, INSUFFICIENT_DATA. Never override an "
            "insufficient-data quality result. Use only supplied evidence. Return JSON only as "
            "{\"decision\": \"...\", \"evidence\": [\"...\"]}.\n"
            f"window_id={window_id}\n"
            f"observations={json.dumps(observations, ensure_ascii=False)}"
        )
        self._record_attempt()
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
            self._record_success()
            return {"decision": decision, "evidence": evidence}
        except (URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
            self._record_failure(f"Ollama finalizer fallback: {exc}")
            return self.fallback.finalize(window_id, observations)


def build_planner(mode: str, ollama_base_url: str, ollama_model: str) -> Planner:
    if mode == "heuristic":
        return HeuristicPlanner()
    if mode == "ollama":
        return OllamaPlanner(ollama_base_url, ollama_model)
    raise ValueError("LLM_MODE must be either 'heuristic' or 'ollama'")
