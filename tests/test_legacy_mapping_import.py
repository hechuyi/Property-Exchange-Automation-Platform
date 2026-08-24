from __future__ import annotations

import unittest

from desktop_backend.domain.legacy_mapping_import import _normalize_row


class LegacyMappingImportTest(unittest.TestCase):
    def test_normalize_row_rejects_non_object_rows(self) -> None:
        for row in (None, [], [("name", "legacy")], "name=legacy"):
            with self.subTest(row=row):
                with self.assertRaisesRegex(TypeError, "legacy mapping row must be an object"):
                    _normalize_row(row)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
