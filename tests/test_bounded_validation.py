from __future__ import annotations

import unittest

from acr_runtime.bounded_validation import bounded_text, bounded_text_list


class BoundedValidationTests(unittest.TestCase):
    def test_text_is_trimmed_bounded_and_secret_safe(self) -> None:
        self.assertEqual(
            bounded_text("  retained evidence  ", field="evidence"),
            "retained evidence",
        )
        with self.assertRaisesRegex(ValueError, "bounded non-empty"):
            bounded_text("   ", field="evidence")
        with self.assertRaisesRegex(ValueError, "bounded non-empty"):
            bounded_text("abcd", field="evidence", maximum=3)
        with self.assertRaises(ValueError):
            bounded_text(
                "api_key=sk-" + ("a" * 32),
                field="evidence",
            )

    def test_list_preserves_contract_and_rejects_duplicates(self) -> None:
        self.assertEqual(
            bounded_text_list(
                [" first ", "second"],
                field="items",
                minimum=0,
                item_maximum=16,
            ),
            ("first", "second"),
        )
        with self.assertRaisesRegex(ValueError, "contains duplicates"):
            bounded_text_list(["same", "same"], field="items")
        with self.assertRaisesRegex(ValueError, "0 to 1 items"):
            bounded_text_list(
                ["one", "two"],
                field="items",
                minimum=0,
                maximum=1,
            )


if __name__ == "__main__":
    unittest.main()
