import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend import council, openrouter


class TestOpenRouterOverflowDetection(unittest.TestCase):
    def test_detects_known_overflow_code(self):
        self.assertTrue(
            openrouter.is_context_overflow_error(
                "context_length_exceeded",
                "any message",
            )
        )

    def test_detects_overflow_message_hint(self):
        self.assertTrue(
            openrouter.is_context_overflow_error(
                None,
                "Prompt exceeds model context length. Please reduce the length or enable middle-out compression.",
            )
        )

    def test_non_overflow_is_false(self):
        self.assertFalse(
            openrouter.is_context_overflow_error(
                "invalid_api_key",
                "Invalid API key",
            )
        )


class TestStage2MiddleOutPolicy(unittest.IsolatedAsyncioTestCase):
    async def _collect_stage2_results(self, query_model_mock):
        settings = SimpleNamespace(
            stage2_prompt="{responses_text}\n\nFINAL RANKING:",
            stage2_temperature=0.3,
        )
        stage1_results = [
            {"model": "openrouter:test-model", "response": "Alpha response", "error": None},
        ]

        with patch("backend.council.get_settings", return_value=settings):
            with patch("backend.council.openrouter.query_model", query_model_mock):
                generator = council.stage2_collect_rankings("Which is best?", stage1_results)
                _ = await generator.__anext__()  # label_to_model
                result = await generator.__anext__()
                return result

    async def test_retry_on_overflow_retries_once_with_middle_out(self):
        first = {
            "content": None,
            "error": "bad_request",
            "error_message": "Prompt exceeds model context length.",
            "error_code": "context_length_exceeded",
            "is_context_overflow": True,
        }
        second = {
            "content": "FINAL RANKING:\n1. Response A",
            "error": None,
        }
        query_model_mock = AsyncMock(side_effect=[first, second])

        result = await self._collect_stage2_results(query_model_mock)

        self.assertEqual(query_model_mock.await_count, 2)
        self.assertEqual(query_model_mock.await_args_list[1].kwargs.get("transforms"), ["middle-out"])
        self.assertEqual(result["error"], None)
        self.assertTrue(result["stage2_transform_applied"])
        self.assertEqual(result["stage2_retry_reason"], "context_overflow")

    async def test_retry_on_overflow_does_not_retry_non_overflow(self):
        first = {
            "content": None,
            "error": "bad_request",
            "error_message": "Invalid request payload",
            "error_code": "invalid_request_error",
            "is_context_overflow": False,
        }
        query_model_mock = AsyncMock(return_value=first)

        result = await self._collect_stage2_results(query_model_mock)

        self.assertEqual(query_model_mock.await_count, 1)
        self.assertTrue(result["error"])
        self.assertFalse(result["stage2_transform_applied"])
        self.assertEqual(result["stage2_retry_reason"], None)

    async def test_non_openrouter_model_does_not_apply_middle_out(self):
        settings = SimpleNamespace(
            stage2_prompt="{responses_text}\n\nFINAL RANKING:",
            stage2_temperature=0.3,
        )
        stage1_results = [
            {"model": "requesty:test-model", "response": "Alpha response", "error": None},
        ]
        query_model_mock = AsyncMock(
            return_value={"content": "FINAL RANKING:\n1. Response A", "error": None}
        )

        with patch("backend.council.get_settings", return_value=settings):
            with patch("backend.council.query_model", query_model_mock):
                generator = council.stage2_collect_rankings("Which is best?", stage1_results)
                _ = await generator.__anext__()
                result = await generator.__anext__()

        self.assertEqual(query_model_mock.await_count, 1)
        self.assertEqual(result["error"], None)
        self.assertFalse(result["stage2_transform_applied"])
        self.assertEqual(result["stage2_retry_reason"], None)


if __name__ == "__main__":
    unittest.main()
