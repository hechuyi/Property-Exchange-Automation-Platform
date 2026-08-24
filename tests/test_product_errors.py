from __future__ import annotations

import unittest

from desktop_backend.product_errors import ProductError


class ProductErrorTest(unittest.TestCase):
    def test_details_defaults_to_empty_dict_when_none(self) -> None:
        error = ProductError("failed", details=None)

        self.assertEqual(error.details, {})

    def test_details_dict_is_copied(self) -> None:
        details = {"field": "source_id"}

        error = ProductError("failed", details=details)
        details["field"] = "changed"

        self.assertEqual(error.details, {"field": "source_id"})

    def test_details_rejects_explicit_non_mapping_values(self) -> None:
        for details in ([], "field=source_id"):
            with self.subTest(details=details):
                with self.assertRaisesRegex(TypeError, "details must be a dict"):
                    ProductError("failed", details=details)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
