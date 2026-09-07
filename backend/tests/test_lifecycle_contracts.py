from __future__ import annotations

import unittest

from app.api.v1 import memory as memory_api
from app.api.v1 import trash as trash_api
from app.api.v1.memory import PromptMemoryWindowRequest
from app.api.v1.trash import RestoreRequest


class TestLifecycleContracts(unittest.TestCase):
    def test_routes_are_registered_once_and_policy_precedes_dynamic_item_route(self):
        from fastapi.routing import APIRoute
        paths = [(r.path, tuple(sorted(getattr(r, "methods", set()))))
                 for r in trash_api.router.routes if isinstance(r, APIRoute)]
        self.assertIn(("/trash/policy", ("GET",)), paths)
        self.assertIn(("/trash/policy", ("PUT",)), paths)
        self.assertIn(("/trash/{item_id}", ("GET",)), paths)
        policy_index = next(i for i, x in enumerate(paths)
                            if x == ("/trash/policy", ("GET",)))
        item_index = next(i for i, x in enumerate(paths)
                          if x == ("/trash/{item_id}", ("GET",)))
        self.assertLess(policy_index, item_index)
        memory_paths = [r.path for r in memory_api.router.routes if isinstance(r, APIRoute)]
        self.assertEqual(memory_paths.count("/memory/prompt-profile"), 1)

    def test_request_bounds_match_product_policy(self):
        self.assertEqual(PromptMemoryWindowRequest(window_size=15).window_size, 15)
        self.assertEqual(RestoreRequest(workspace_ids=[]).workspace_ids, [])
        with self.assertRaises(Exception):
            PromptMemoryWindowRequest(window_size=4)
        with self.assertRaises(Exception):
            RestoreRequest(workspace_ids=["x"] * 101)


if __name__ == "__main__":
    unittest.main()
