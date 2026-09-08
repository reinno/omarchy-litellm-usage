import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import date

spec = importlib.util.spec_from_file_location("collector", Path(__file__).resolve().parents[1] / "collector.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
TOKEN = "synthetic-test-token"
HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


def row(tokens=100, spend=1.25, key=HASH):
    metrics = dict(total_tokens=tokens, prompt_tokens=tokens - 10, completion_tokens=10,
                   cache_read_input_tokens=30, cache_creation_input_tokens=20, spend=spend)
    return {"date": "2026-01-07", "breakdown": {
        "api_keys": {key: {"metrics": metrics}},
        "models": {"test-model": {"api_key_breakdown": {key: {"metrics": metrics}}}}}}


class FakeClient:
    def __init__(self, pages=None, budget=None):
        self.pages = pages or [{"results": [row()], "metadata": {"total_pages": 1}}]
        self.budget = budget
        self.calls = []

    def get(self, path, **query):
        self.calls.append((path, query))
        if path == "/key/info":
            return {"info": {"user_id": "synthetic-user", "spend": 25, "max_budget": self.budget,
                             "budget_reset_at": "2026-02-01T00:00:00Z"}}
        return self.pages[query["page"] - 1]


class CollectorTests(unittest.TestCase):
    def test_native_record_and_cache_tokens_not_double_counted(self):
        result = c.collect(FakeClient(), TOKEN, date(2026, 1, 7))
        self.assertEqual(result["todayTotalTokens"], 100)
        self.assertEqual(sum(result["modelUsage"]["test-model"].values()), 100)
        self.assertEqual(len(result["recentDays"]), 7)
        self.assertFalse(result["hasPromptStats"])
        self.assertEqual(result["scope"], "account")
        self.assertEqual(result["limits"], [])
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(HASH, json.dumps(result))

    def test_nested_pagination_and_multiple_rows_per_day(self):
        client = FakeClient([
            {"results": [row()], "metadata": {"total_pages": 2, "has_more": True}},
            {"results": [row(200)], "metadata": {"total_pages": 2, "has_more": False}},
        ])
        result = c.collect(client, TOKEN, date(2026, 1, 7))
        self.assertEqual(result["todayTotalTokens"], 300)
        self.assertEqual(result["todayTokensByModel"]["test-model"], 300)
        self.assertIn("$2.50 today", result["tierLabel"])
        self.assertEqual(client.calls[1][1]["user_id"], "synthetic-user")

    def test_other_keys_excluded_even_for_same_user(self):
        result = c.collect(FakeClient([{"results": [row(key="other-key"), row()]}]), TOKEN, date(2026, 1, 7))
        self.assertEqual(result["todayTotalTokens"], 100)
        self.assertEqual(sum(result["modelUsage"]["test-model"].values()), 100)

    def test_budget_is_a_limit_not_prepaid_balance(self):
        result = c.collect(FakeClient(budget=100), TOKEN, date(2026, 1, 7))
        self.assertEqual(result["limits"][0]["percent"], .25)
        self.assertEqual(result["limits"][0]["resetsAt"], "2026-02-01T00:00:00Z")
        self.assertNotIn("balance", result)

    def test_zero_and_exceeded_budget(self):
        zero = c.collect(FakeClient(budget=0), TOKEN, date(2026, 1, 7))
        self.assertEqual(zero["limits"], [])
        self.assertIn("$0.00 budget", zero["tierLabel"])
        exceeded = c.collect(FakeClient(budget=10), TOKEN, date(2026, 1, 7))
        self.assertEqual(exceeded["limits"][0]["percent"], 2.5)

    def test_missing_key_breakdown_is_not_reported_as_zero(self):
        with self.assertRaises(c.UsageError):
            c.collect(FakeClient([{"results": [{"date": "2026-01-07", "breakdown": {}}]}]), TOKEN, date(2026, 1, 7))

    def test_incomplete_pagination_fails(self):
        with self.assertRaises(c.UsageError):
            c.collect(FakeClient([{"results": [], "metadata": {"has_more": True}}]), TOKEN, date(2026, 1, 7))

    def test_nonfinite_numbers_rejected(self):
        for value in (float("nan"), float("inf"), -1, "bad", None):
            with self.assertRaises(c.UsageError):
                c.number(value)

    def test_redirect_never_forwards_credentials(self):
        with self.assertRaises(c.UsageError):
            c.NoRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere.example")


class ConfigAndStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.target = Path(self.temp.name) / "state/litellm.json"
        self.env = patch.dict(os.environ, {"LITELLM_URL": "https://example.com", "LITELLM_TOKEN": TOKEN}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_environment_configuration(self):
        self.assertEqual(c.read_settings(self.path), ("https://example.com", TOKEN))

    def test_explicit_url_and_custom_token_environment(self):
        self.path.write_text(json.dumps({"url": "https://proxy.example/api/", "tokenEnv": "CUSTOM_TOKEN"}))
        os.environ["CUSTOM_TOKEN"] = "custom-token"
        self.assertEqual(c.read_settings(self.path), ("https://proxy.example/api", "custom-token"))

    def test_private_token_file_and_environment_precedence(self):
        token_file = Path(self.temp.name) / "key"
        token_file.write_text("file-token\n")
        token_file.chmod(0o600)
        self.path.write_text(json.dumps({"tokenFile": str(token_file)}))
        self.assertEqual(c.read_settings(self.path)[1], TOKEN)
        del os.environ["LITELLM_TOKEN"]
        self.assertEqual(c.read_settings(self.path)[1], "file-token")
        token_file.chmod(0o644)
        with self.assertRaises(c.UsageError):
            c.read_settings(self.path)

    def test_invalid_configuration_and_remote_http_rejected(self):
        for config in ([], {"url": "http://example.com"}, {"url": "https://user:pass@example.com"},
                       {"url": "https://example.com?key=secret"}, {"tokenEnv": []}):
            self.path.write_text(json.dumps(config))
            with self.assertRaises(c.UsageError):
                c.read_settings(self.path)
        self.path.write_text('{"url":"http://127.0.0.1:4000"}')
        self.assertEqual(c.read_settings(self.path)[0], "http://127.0.0.1:4000")

    def test_failure_preserves_only_matching_account_and_marks_stale(self):
        with patch.object(c, "Client", return_value=FakeClient()):
            status, good = c.refresh(self.path, self.target)
        self.assertEqual(status, 0)
        with patch.object(c, "Client", side_effect=RuntimeError("secret diagnostic " + TOKEN)):
            status, stale = c.refresh(self.path, self.target)
            self.assertEqual(status, 1)
            self.assertTrue(stale["stale"])
            self.assertFalse(stale["ready"])
            self.assertEqual(stale["updatedAt"], good["updatedAt"])
            self.assertNotIn(TOKEN, self.target.read_text())
            os.environ["LITELLM_TOKEN"] = "different-key"
            _, changed = c.refresh(self.path, self.target)
            self.assertFalse(changed["stale"])
            self.assertEqual(changed["modelUsage"], {})

    def test_atomic_output_has_private_permissions_and_no_temporary_files(self):
        c.write_record(self.target, c.empty_record())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.target.parent.iterdir()), [self.target])
        self.assertEqual(json.loads(self.target.read_text())["id"], "litellm")


if __name__ == "__main__":
    unittest.main()
