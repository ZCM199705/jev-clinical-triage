import json
import unittest
from decimal import Decimal

from runtime.adapters import (
    parse_response, payload, reservation, response_cost, validate_identity,
)


class FormalAdapterTests(unittest.TestCase):
    CASE = {"case_text": "Synthetic case: mild symptoms; choose urgency."}

    def test_payload_is_whitelisted_and_frozen(self):
        case = dict(self.CASE, gold="LEAK", case_id="PRIVATE", diagnosis="DIAG", original_response="OLD")
        for role in ("jev", "luna", "gemini", "deepseek"):
            body = payload(role, case)
            text = json.dumps(body, ensure_ascii=False)
            for marker in ("LEAK", "PRIVATE", "DIAG", "OLD"):
                self.assertNotIn(marker, text)
            self.assertIn(self.CASE["case_text"], text)
        self.assertEqual(payload("luna", self.CASE)["provider"]["allow_fallbacks"], False)
        self.assertEqual(payload("luna", self.CASE)["reasoning"], {"effort": "none"})
        self.assertEqual(payload("gemini", self.CASE)["reasoning"], {"effort": "minimal"})
        self.assertEqual(payload("deepseek", self.CASE)["thinking"], {"type": "disabled"})

    def test_malicious_model_and_condition_are_rejected(self):
        with self.assertRaises(ValueError): payload("openai/gpt-5.6-luna", self.CASE)
        with self.assertRaises(NotImplementedError): payload("luna", self.CASE, "format")
        with self.assertRaises(ValueError): payload("luna", {"case_text": ""})

    def test_jev_probability_schema_and_nonfinite_values(self):
        base = {"model": "typesafe/jev-1.13-20260917", "provider": "TypeSafe",
                "answers": {"triage": {"choice": "A", "probabilities":
                {"A": .9, "B": .05, "C": .03, "D": .02}, "confidence": .8}}}
        parsed = parse_response("jev", base)
        self.assertEqual(parsed["triage"], "A")
        bad = json.loads(json.dumps(base))
        bad["answers"]["triage"]["probabilities"]["A"] = float("nan")
        with self.assertRaises(ValueError): parse_response("jev", bad)
        bad["answers"]["triage"]["probabilities"] = {"A": .4, "B": .2, "C": .2, "D": .2}
        bad["answers"]["triage"]["choice"] = "D"
        with self.assertRaises(ValueError): parse_response("jev", bad)

    def test_chat_schema_requires_complete_stop(self):
        good = {"model": "openai/gpt-5.6-luna", "provider": "OpenAI",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"triage":"C"}'}}]}
        self.assertEqual(parse_response("luna", good)["triage"], "C")
        for finish in ("length", None):
            bad = dict(good, choices=[dict(good["choices"][0], finish_reason=finish)])
            with self.assertRaises(ValueError): parse_response("luna", bad)

    def test_identity_requires_model_and_provider(self):
        validate_identity("gemini", {"model": "google/gemini-3.1-flash-lite", "provider": "Google AI Studio"})
        validate_identity("deepseek", {"model": "deepseek-flash"})
        with self.assertRaises(ValueError): validate_identity("luna", {"model": "openai/gpt-5.6-luna", "provider": "Azure"})
        with self.assertRaises(ValueError): validate_identity("luna", {"model": "other", "provider": "OpenAI"})

    def test_cost_parsing_and_deepseek_conservative_estimate(self):
        self.assertEqual(response_cost("luna", {"usage": {"cost": "0.0012"}}), ("0.0012", "provider_reported"))
        self.assertEqual(response_cost("luna", {"usage": {"cost": "NaN"}}), (None, "unknown"))
        cost, status = response_cost("deepseek", {"usage": {"prompt_tokens": 100, "completion_tokens": 20}})
        self.assertEqual(status, "estimated_upper_peak_uncached")
        self.assertEqual(cost, "0.000054")
        self.assertEqual(response_cost("deepseek", {"usage": {"prompt_tokens": True, "completion_tokens": 1}}), (None, "unknown"))

    def test_reservation_uses_utf8_wrapper_buffer_and_output_cap(self):
        body = payload("luna", self.CASE)
        reserve = reservation("luna", body)
        self.assertIsInstance(reserve, Decimal)
        self.assertGreater(reserve, Decimal("0"))
        with self.assertRaises(ValueError): reservation("luna", body, 4097)
        with self.assertRaises(ValueError): reservation("luna", {"x": "x" * 20000})


if __name__ == "__main__":
    unittest.main()
