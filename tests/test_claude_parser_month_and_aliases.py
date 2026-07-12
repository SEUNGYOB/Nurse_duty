import unittest

from ocr.claude_parser import (
    build_prompt,
    build_shift_lookup,
    days_in_month,
    normalize_rows,
)


class ClaudeParserMonthAndAliasTests(unittest.TestCase):
    def test_days_in_month_handles_31_day_months(self):
        self.assertEqual(days_in_month(2026, 7), 31)
        self.assertEqual(days_in_month(2026, 2), 28)

    def test_build_prompt_uses_requested_column_count(self):
        prompt = build_prompt(2026, 7, column_count=31)
        self.assertIn("1~31", prompt)
        self.assertIn("exactly 31 items", prompt)

    def test_normalize_rows_keeps_31_columns_and_custom_aliases(self):
        lookup = build_shift_lookup({
            "day": ["데이"],
            "evening": ["이브닝"],
            "night": ["나이트"],
            "s": ["S"],
            "annual": ["연차"],
            "off": ["휴무"],
        })
        payload = {
            "rows": [
                {
                    "rowIndex": 1,
                    "name": "민지",
                    "shifts": ["데이", "이브닝", "나이트", "S", "연차", "휴무"] + ["D"] * 25,
                }
            ]
        }

        rows = normalize_rows(payload, column_count=31, shift_lookup=lookup)
        self.assertEqual(len(rows[0]["shifts"]), 31)
        self.assertEqual(rows[0]["shifts"][:6], ["D", "E", "N", "S", "Y", "off"])


if __name__ == "__main__":
    unittest.main()
