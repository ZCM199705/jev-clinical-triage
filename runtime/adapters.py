"""Offline request/response contracts for the four authorised formal models.

This module deliberately contains no HTTP client and no credential handling.  A
caller may use the returned body with a separately approved transport, then
validate the complete response before recording it.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation, ROUND_UP
from typing import Any


MAX_OUTPUT_TOKENS = 4096
MAX_INPUT_BYTES = 16000
_OPTIONS = {
    "A": "Fine to monitor at home",
    "B": "See my doctor in the next few weeks",
    "C": "See a doctor within 24–48 hours",
    "D": "Go to the ER now",
}
_TASK = (
    "Based only on the case information, choose the most appropriate urgency "
    "of care for the current condition. Do not infer unprovided findings. "
    "Use exactly one of these options: "
    + " ".join(f"{k}: {v}" for k, v in _OPTIONS.items())
    + "."
)

# These are the tested, authorised route/model/provider combinations.  The
# canonical slug is a returned-model identity, not a request model id.
_MODELS = {
    "jev": {
        "requested": "typesafe/jev-1.13",
        "canonical": "typesafe/jev-1.13-20260917",
        "endpoint": "https://openrouter.ai/api/v1/systemone",
        "provider": "TypeSafe",
        "price_in": "0.042", "price_out": "0",
    },
    "luna": {
        "requested": "openai/gpt-5.6-luna",
        "canonical": "openai/gpt-5.6-luna-20260709",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "provider": "OpenAI", "provider_tag": "openai",
        "price_in": "0.20", "price_out": "1.20",
    },
    "gemini": {
        "requested": "google/gemini-3.1-flash-lite",
        "canonical": "google/gemini-3.1-flash-lite-20260507",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "provider": "Google AI Studio", "provider_tag": "google-ai-studio",
        "price_in": "0.25", "price_out": "1.50",
    },
    "deepseek": {
        "requested": "deepseek-flash", "canonical": "deepseek-flash",
        "endpoint": "https://api.deepseek.com/chat/completions",
        "provider": "DeepSeek", "price_in": "0.30", "price_out": "1.20",
    },
}


def _model(model: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    role = model if isinstance(model, str) else model.get("role")
    if role not in _MODELS:
        raise ValueError("unauthorised_model")
    # A dict is accepted for ergonomic integration, but its frozen identity is
    # checked so callers cannot silently substitute an arbitrary model.
    if isinstance(model, dict):
        frozen = _MODELS[role]
        if any(model.get(k) not in (None, frozen[v]) for k, v in (
            ("model_requested", "requested"), ("endpoint", "endpoint"),
        )):
            raise ValueError("unauthorised_model_identity")
    return role, _MODELS[role]


def _check_limit(max_output_tokens: int) -> None:
    if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int):
        raise ValueError("invalid_output_limit")
    if max_output_tokens < 1 or max_output_tokens > MAX_OUTPUT_TOKENS:
        raise ValueError("output_limit_exceeds_authorised_maximum")


def payload(model, case, condition: str = "structured", max_output_tokens: int = 4096) -> dict:
    role, spec = _model(model)
    _check_limit(max_output_tokens)
    if condition != "structured":
        raise NotImplementedError("only_structured_condition_is_authorised")
    if not isinstance(case, dict) or not isinstance(case.get("case_text"), str) or not case["case_text"]:
        raise ValueError("case_text_required")
    if role == "jev":
        return {"model": spec["requested"], "state": case["case_text"],
                "questions": {"triage": {"type": "choice", "instructions": _TASK,
                                             "criteria": dict(_OPTIONS)}}}
    body = {
        "model": spec["requested"],
        "messages": [
            {"role": "system", "content": _TASK +
             ' Return exactly one JSON object with one key: {"triage":"A"}. '
             "The value must be A, B, C, or D. Do not explain."},
            {"role": "user", "content": case["case_text"]},
        ],
        "max_tokens": max_output_tokens, "stream": False,
        "response_format": {"type": "json_object"},
    }
    if role in {"luna", "gemini"}:
        tag = spec["provider_tag"]
        body["provider"] = {"only": [tag], "order": [tag],
                             "allow_fallbacks": False, "require_parameters": True,
                             "ignore": [tag + "/flex", tag + "/fast", tag + "/priority"]}
        body["reasoning"] = {"effort": "none" if role == "luna" else "minimal"}
    else:
        body["thinking"] = {"type": "disabled"}
    return body


def reservation(model, body, max_output_tokens: int = 4096) -> Decimal:
    role, spec = _model(model)
    _check_limit(max_output_tokens)
    if not isinstance(body, dict):
        raise ValueError("request_body_required")
    if body.get("model") != spec["requested"]:
        raise ValueError("reservation_model_mismatch")
    if role != "jev" and body.get("max_tokens") != max_output_tokens:
        raise ValueError("reservation_output_limit_mismatch")
    # Match the runner's ordinary JSON encoding; retaining its whitespace is
    # conservative when the transport serializes the returned mapping.
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    upper_input = len(raw) + MAX_OUTPUT_TOKENS
    if upper_input > MAX_INPUT_BYTES:
        raise ValueError("formal_input_too_large")
    try:
        ip, op = Decimal(spec["price_in"]), Decimal(spec["price_out"])
    except (InvalidOperation, KeyError) as exc:
        raise ValueError("unknown_price") from exc
    if not ip.is_finite() or not op.is_finite() or ip < 0 or op < 0:
        raise ValueError("unknown_price")
    value = (Decimal(upper_input) * ip * Decimal("1.25") +
             Decimal(max_output_tokens) * op) / Decimal(1000000)
    return value.quantize(Decimal("0.000001"), rounding=ROUND_UP)


def _probabilities(values: Any) -> dict[str, float]:
    if not isinstance(values, dict) or set(values) != set(_OPTIONS):
        raise ValueError("choice_probability_schema")
    out = {}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid_probability")
        out[key] = float(value)
    if abs(sum(out.values()) - 1.0) > 0.002:
        raise ValueError("probabilities_not_normalized")
    return out


def parse_response(model, data, condition: str = "structured") -> dict:
    role, _ = _model(model)
    if condition != "structured":
        raise NotImplementedError("only_structured_condition_is_authorised")
    if not isinstance(data, dict):
        raise ValueError("response_object_required")
    if role == "jev":
        try:
            answer = data["answers"]["triage"]
            triage, probs = answer["choice"], _probabilities(answer["probabilities"])
        except (KeyError, TypeError) as exc:
            raise ValueError("incomplete_jev_response") from exc
        if triage not in _OPTIONS:
            raise ValueError("invalid_choice")
        if probs[triage] + 1e-9 < max(probs.values()):
            raise ValueError("choice_not_probability_maximum")
        return {"triage": triage, "probabilities": probs,
                "provider_confidence": answer.get("confidence")}
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("incomplete_or_refused_answer")
        content = choice["message"]["content"]
        result = json.loads(content) if isinstance(content, str) else content
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("incomplete_structured_response") from exc
    if not isinstance(result, dict) or set(result) != {"triage"} or result["triage"] not in _OPTIONS:
        raise ValueError("invalid_triage_json")
    return {"triage": result["triage"], "probabilities": None,
            "provider_confidence": None}


def response_cost(model, data) -> tuple[str | None, str]:
    role, spec = _model(model)
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None, "unknown"
    if role != "deepseek":
        value = usage.get("cost")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None, "unknown"
        try:
            cost = Decimal(str(value))
        except InvalidOperation:
            return None, "unknown"
        return (str(cost), "provider_reported") if cost.is_finite() and cost >= 0 else (None, "unknown")
    i, o = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (i, o)):
        return None, "unknown"
    cost = (Decimal(i) * Decimal("0.30") + Decimal(o) * Decimal("1.20")) / Decimal(1000000)
    return str(cost), "estimated_upper_peak_uncached"


def validate_identity(model, data) -> None:
    role, spec = _model(model)
    if not isinstance(data, dict) or data.get("model") not in {spec["requested"], spec["canonical"]}:
        raise ValueError("unexpected_returned_model")
    provider = data.get("provider")
    if role == "deepseek" and provider is None:
        return
    if provider != spec["provider"]:
        raise ValueError("unexpected_returned_provider")
