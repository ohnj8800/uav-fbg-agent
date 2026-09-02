from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    analysis_base_url: str = "http://analysis-service:8001"
    llm_mode: str = "heuristic"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"
    max_steps: int = 6
    audit_log_path: Path = Path("runtime/audit.jsonl")
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            analysis_base_url=os.getenv(
                "ANALYSIS_BASE_URL", "http://analysis-service:8001"
            ).rstrip("/"),
            llm_mode=os.getenv("LLM_MODE", "heuristic").strip().lower(),
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL", "http://host.docker.internal:11434"
            ).rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
            max_steps=int(os.getenv("AGENT_MAX_STEPS", "6")),
            audit_log_path=Path(os.getenv("AUDIT_LOG_PATH", "runtime/audit.jsonl")),
            host=os.getenv("AGENT_HOST", "0.0.0.0"),
            port=int(os.getenv("AGENT_PORT", "8000")),
        )

