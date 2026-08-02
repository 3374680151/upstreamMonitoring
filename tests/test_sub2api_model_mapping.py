import unittest

from app import parse_sub2api_monitor_models


class Sub2ApiMonitorMappingTests(unittest.TestCase):
    def test_monitor_name_resolves_ambiguous_platform_groups(self):
        groups = {
            "GPT-plus": {"id": 2, "platform": "openai", "ratio": 0.04},
            "GPT-pro": {"id": 8, "platform": "openai", "ratio": 0.1},
            "gpt-特价": {"id": 11, "platform": "openai", "ratio": 0.03},
            "Grok": {"id": 5, "platform": "grok", "ratio": 0.01},
        }
        monitors = {
            "items": [
                {"name": "gpt特价0.03", "provider": "openai", "primary_model": "gpt-5.5"},
                {"name": "GPT-pro(0.1)", "provider": "openai", "primary_model": "gpt-5.5"},
                {"name": "GPT-plus(0.04)", "provider": "openai", "primary_model": "gpt-5.5"},
                {"name": "Grok(0.01)", "provider": "grok", "primary_model": "grok-4.5"},
            ]
        }

        models, unmatched = parse_sub2api_monitor_models(monitors, groups)

        self.assertEqual(unmatched, [])
        self.assertEqual(models["gpt-特价"][0]["name"], "gpt-5.5")
        self.assertEqual(models["GPT-pro"][0]["name"], "gpt-5.5")
        self.assertEqual(models["GPT-plus"][0]["name"], "gpt-5.5")
        self.assertEqual(models["Grok"][0]["name"], "grok-4.5")


if __name__ == "__main__":
    unittest.main()
