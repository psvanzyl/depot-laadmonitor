"""Optional LLM classifier for free-text permit notices (flag: LLM_CLASSIFY).

Design rules from the brief:
  * never in the hot path,
  * only for free-text permit notices,
  * results cached by prompt hash,
  * every prompt and output logged (llm_cache is the log).

The model is asked for a single label from a closed vocabulary plus a one-line reason.
Anything unexpected is ignored (fail-closed to the rule-based result).
"""

from __future__ import annotations

import hashlib
import json
import logging

import httpx

from ..config import Settings
from ..store import Store

log = logging.getLogger(__name__)

LABELS = [
    "rumoured",
    "permit_requested",
    "permit_granted",
    "grid_connection_signalled",
    "under_construction",
    "operational",
    "unknown",
]

SYSTEM = (
    "Je classificeert Nederlandse omgevingsvergunningen voor laadinfrastructuur voor "
    "zware vrachtwagens. Antwoord met exact één label uit de lijst en niets anders."
)

PROMPT = """Classificeer de fase van de laadlocatie op basis van de bekendmaking.

Labels: {labels}

Tekst:
---
{text}
---

Antwoord met één label."""


class PermitClassifier:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.enabled = bool(settings.llm_classify and settings.llm_base_url)

    def _cache_get(self, key: str) -> str | None:
        with self.store.conn.cursor() as cur:
            cur.execute("SELECT output FROM llm_cache WHERE input_hash=%s", (key,))
            row = cur.fetchone()
        return row["output"] if row else None

    def _cache_put(self, key: str, prompt: str, output: str) -> None:
        with self.store.tx() as cur:
            cur.execute(
                """INSERT INTO llm_cache(input_hash, model, prompt, output)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (input_hash) DO NOTHING""",
                (key, self.settings.llm_model, prompt, output),
            )

    def classify(self, text: str) -> str | None:
        if not self.enabled or not text:
            return None
        prompt = PROMPT.format(labels=", ".join(LABELS), text=text[:4000])
        key = hashlib.sha256(f"{self.settings.llm_model}:{prompt}".encode()).hexdigest()
        cached = self._cache_get(key)
        if cached is not None:
            return cached if cached in LABELS else None
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        try:
            resp = httpx.post(
                f"{self.settings.llm_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": self.settings.llm_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 16,
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip().lower()
        except Exception as exc:
            log.warning("LLM classify failed: %s", exc)
            return None
        label = next((l for l in LABELS if l in content), None)
        self._cache_put(key, prompt, label or content[:100])
        return label

    def classify_site(self, texts: list[str]) -> str | None:
        """Classify from the first permit text that yields a label."""
        for text in texts[:3]:
            label = self.classify(text)
            if label:
                return label
        return None
