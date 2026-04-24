from __future__ import annotations

import importlib
import unittest

from tests.hymer_test_support import ensure_package_paths, install_homeassistant_stubs


ensure_package_paths()
install_homeassistant_stubs()

api = importlib.import_module("custom_components.hymer_connect_metadata.api")


class ApiTitleTests(unittest.TestCase):
    def test_vehicle_title_prefers_model_and_vin_suffix(self) -> None:
        title = api.HymerConnectApi._build_vehicle_title(
            {
                "name": "My Camper",
                "model": "BMC-i 680",
                "vin": "WDB123456789ABCDEF",
            }
        )
        self.assertEqual(title, "BMC-i 680 (ABCDEF)")

    def test_vehicle_title_falls_back_to_model_without_vin(self) -> None:
        title = api.HymerConnectApi._build_vehicle_title(
            {
                "name": "My Camper",
                "model": "ML-T 580",
                "vin": "",
            }
        )
        self.assertEqual(title, "ML-T 580")


if __name__ == "__main__":
    unittest.main()
