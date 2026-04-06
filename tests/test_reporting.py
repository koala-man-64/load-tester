from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from loadtester.reporting import create_run_output_dir, slugify_service_name


class ReportingTests(unittest.TestCase):
    def test_slugify_service_name(self) -> None:
        self.assertEqual(slugify_service_name("Orders API Create Order"), "orders-api-create-order")

    def test_create_run_output_dir_uses_timestamp_and_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            started_at = datetime(2026, 4, 6, 19, 5, 31, tzinfo=timezone.utc)

            run_dir = create_run_output_dir(root, "Orders API", started_at)

            self.assertTrue(run_dir.exists())
            self.assertEqual(run_dir.name, "2026-04-06T19-05-31_orders-api")
