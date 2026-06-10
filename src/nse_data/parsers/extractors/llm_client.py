"""Azure OpenAI client with cost tracking and daily spend cap.

Phase 3 uses this for LLM-assisted labeling drafts (offline batch work,
~$2-5 total). Phase 5 will reuse this for live vision-based extraction
escalation on hard-to-parse PDFs.

Why a wrapper class instead of using AzureOpenAI() directly:
  - Daily spend cap enforced in code (cannot exceed $1/day default)
  - Per-call cost logged for audit
  - Single point to swap providers later if needed
  - Token usage captured for the LLM_input_tokens / LLM_output_tokens DB columns
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from openai import AzureOpenAI

LOG = logging.getLogger(__name__)


# Azure OpenAI pricing as of 2026 — gpt-4o.
# These are per-token rates (USD). Update if Azure changes pricing.
# Source: azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/
GPT4O_PRICE_PER_INPUT_TOKEN = 2.50 / 1_000_000   # $2.50 per 1M input tokens
GPT4O_PRICE_PER_OUTPUT_TOKEN = 10.00 / 1_000_000  # $10.00 per 1M output tokens

# Daily spending cap. Hardcoded for safety; can be raised but the limit
# stays in code, not config (intentional — prevents accidental cap bypass).
# Raised 10 → 25 (2026-06, user call: accuracy over cost) — a heavy results
# day now spends three ways (P&L vision, narrative reads + siblings, deck
# vision) and a cap-hit degrades narrative accuracy to regex-only.
DEFAULT_DAILY_CAP_USD = 25.0

# Where the cumulative spend tracker lives.
SPEND_LOG_PATH = Path("data/llm_spend.json")


@dataclass
class LLMCallResult:
    """One call's outcome — includes content, tokens, and cost."""

    success: bool
    content: Optional[str] = None
    parsed_json: Optional[dict] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None
    raw_response: Optional[Any] = field(default=None, repr=False)


class DailyCapExceeded(Exception):
    """Raised when the daily spend cap would be exceeded."""


class LLMClient:
    """Azure OpenAI client with cost tracking + daily cap.

    Construction reads env vars:
      AZURE_OPENAI_ENDPOINT
      AZURE_OPENAI_API_KEY
      AZURE_OPENAI_API_VERSION
      AZURE_OPENAI_DEPLOYMENT_NAME

    Usage:
        client = LLMClient()
        result = client.chat_completion(
            messages=[{"role": "user", "content": "..."}],
            response_format={"type": "json_object"},
        )
        if result.success:
            print(result.parsed_json)
            print(f"Cost: ${result.cost_usd:.4f}")
    """

    def __init__(
        self,
        daily_cap_usd: float = DEFAULT_DAILY_CAP_USD,
        spend_log_path: Path = SPEND_LOG_PATH,
    ):
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")

        missing = [k for k, v in [
            ("AZURE_OPENAI_ENDPOINT", endpoint),
            ("AZURE_OPENAI_API_KEY", api_key),
            ("AZURE_OPENAI_DEPLOYMENT_NAME", deployment),
        ] if not v]
        if missing:
            raise EnvironmentError(
                f"Missing required env vars: {missing}. "
                f"Check .env file."
            )

        self.client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint,
        )
        self.deployment = deployment
        self.daily_cap_usd = daily_cap_usd
        self.spend_log_path = spend_log_path

    # ------------------------------------------------------------------
    # Spend tracking
    # ------------------------------------------------------------------

    def _load_spend_log(self) -> dict:
        if not self.spend_log_path.exists():
            return {}
        try:
            return json.loads(self.spend_log_path.read_text())
        except (json.JSONDecodeError, OSError):
            LOG.warning("Spend log corrupt at %s; resetting", self.spend_log_path)
            return {}

    def _save_spend_log(self, log: dict) -> None:
        self.spend_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.spend_log_path.write_text(json.dumps(log, indent=2))

    def _today_key(self) -> str:
        return time.strftime("%Y-%m-%d")

    def todays_spend(self) -> float:
        return self._load_spend_log().get(self._today_key(), 0.0)

    def remaining_budget(self) -> float:
        return max(0.0, self.daily_cap_usd - self.todays_spend())

    def _record_spend(self, cost: float) -> None:
        log = self._load_spend_log()
        today = self._today_key()
        log[today] = log.get(today, 0.0) + cost
        self._save_spend_log(log)

    # ------------------------------------------------------------------
    # The actual call
    # ------------------------------------------------------------------

    def chat_completion(
        self,
        messages: list[dict],
        response_format: Optional[dict] = None,
        max_tokens: int = 4000,
        temperature: float = 0.0,
        timeout: float = 60.0,
    ) -> LLMCallResult:
        """One chat completion. Returns LLMCallResult with cost + tokens.

        Raises DailyCapExceeded if the cap would be exceeded before the call.

        On API errors (rate limit, server error), returns LLMCallResult
        with success=False and error message — doesn't raise.
        """
        # Pre-flight: check we haven't already blown the cap
        if self.todays_spend() >= self.daily_cap_usd:
            raise DailyCapExceeded(
                f"Daily LLM spend ${self.todays_spend():.2f} >= "
                f"cap ${self.daily_cap_usd:.2f}. "
                f"Wait until tomorrow or raise the cap."
            )

        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                response_format=response_format,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
        except Exception as e:
            return LLMCallResult(
                success=False,
                error=f"api_call_failed: {e!r}",
            )

        # Extract content + usage
        try:
            content = response.choices[0].message.content
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
        except (AttributeError, IndexError) as e:
            return LLMCallResult(
                success=False,
                error=f"unexpected_response_shape: {e!r}",
                raw_response=response,
            )

        # Calculate cost
        cost = (
            input_tokens * GPT4O_PRICE_PER_INPUT_TOKEN
            + output_tokens * GPT4O_PRICE_PER_OUTPUT_TOKEN
        )
        self._record_spend(cost)

        # Parse JSON if response_format requested it
        parsed_json = None
        if response_format and response_format.get("type") == "json_object":
            try:
                parsed_json = json.loads(content)
            except (json.JSONDecodeError, TypeError) as e:
                return LLMCallResult(
                    success=False,
                    content=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                    error=f"json_parse_failed: {e!r}",
                    raw_response=response,
                )

        return LLMCallResult(
            success=True,
            content=content,
            parsed_json=parsed_json,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            raw_response=response,
        )