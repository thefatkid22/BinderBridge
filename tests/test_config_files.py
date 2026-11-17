import configparser
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ConfigFileTests(unittest.TestCase):
    def test_distributed_config_files_parse_strictly(self):
        for relative_path in (
            "binderbridge.example.ini",
            "deploy/binderbridge.production.ini",
        ):
            with self.subTest(config=relative_path):
                parser = configparser.ConfigParser()
                with (ROOT / relative_path).open(encoding="utf-8") as config_file:
                    parser.read_file(config_file)
                self.assertEqual(
                    parser.get("webhooks", "allow_private_destinations"),
                    "false",
                )
                self.assertEqual(parser.get("webhooks", "worker_enabled"), "true")


if __name__ == "__main__":
    unittest.main()
