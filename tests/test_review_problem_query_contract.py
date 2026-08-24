from __future__ import annotations

import unittest

from desktop_backend.review_problem_contract import normalize_review_problem_query


class ReviewProblemQueryContractTest(unittest.TestCase):
    def test_normalize_review_problem_query_rejects_non_string_filter_values(self) -> None:
        invalid_queries = (
            False,
            {"problem_kind": [False]},
            {"record_family": [{"family": "listing"}]},
            {"state": [1]},
            {"business_id": [False]},
            {"business_id": {"value": "all"}},
            {"exchange": [False]},
            {"keyword": [False]},
            {"keyword": []},
            {"keyword": ()},
            {"keyword": [None]},
            {"keyword": 1},
            {"date_from": [20260517]},
            {"date_to": [False]},
            {"page": [1]},
            {"page_size": [50]},
        )

        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    normalize_review_problem_query(query)


if __name__ == "__main__":
    unittest.main()
