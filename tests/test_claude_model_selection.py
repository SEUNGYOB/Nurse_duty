import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from ocr.claude_parser import (
    build_anthropic_model_candidates,
    select_latest_stable_sonnet_model,
)


class ClaudeModelSelectionTests(unittest.TestCase):
    def test_select_latest_stable_sonnet_model_ignores_preview_and_non_sonnet(self):
        models = [
            SimpleNamespace(
                id="claude-haiku-4-5",
                display_name="Claude Haiku 4.5",
                created_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id="claude-sonnet-4-6",
                display_name="Claude Sonnet 4.6",
                created_at=datetime(2025, 6, 2, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id="claude-sonnet-4-7-preview",
                display_name="Claude Sonnet 4.7 Preview",
                created_at=datetime(2025, 6, 3, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id="claude-sonnet-4-8",
                display_name="Claude Sonnet 4.8",
                created_at=datetime(2025, 6, 4, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id="claude-opus-4-8",
                display_name="Claude Opus 4.8",
                created_at=datetime(2025, 6, 5, tzinfo=timezone.utc),
            ),
        ]

        self.assertEqual(select_latest_stable_sonnet_model(models), "claude-sonnet-4-8")

    def test_build_anthropic_model_candidates_preserves_override_and_dedupes(self):
        candidates = build_anthropic_model_candidates(
            "claude-sonnet-4-6",
            "claude-sonnet-4-8",
            ("claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5"),
        )

        self.assertEqual(
            candidates,
            ("claude-sonnet-4-6", "claude-sonnet-4-8", "claude-opus-4-8", "claude-haiku-4-5"),
        )


if __name__ == "__main__":
    unittest.main()
