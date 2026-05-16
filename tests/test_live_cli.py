from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from trader_ai.cli import live  # noqa: E402


class LiveCliTest(unittest.TestCase):
    def test_load_runtime_env_loads_dotenv_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "TRADING_MODE=paper\n"
                "EXCHANGE_ID=bybit\n"
                "EXCHANGE_API_KEY=dotenv_key\n"
                "EXCHANGE_SECRET_KEY=dotenv_secret\n",
            )

            old_cwd = Path.cwd()
            os.chdir(tmpdir)
            try:
                with patch.dict(
                    os.environ,
                    {
                        "TRADING_MODE": "",
                        "EXCHANGE_ID": "",
                        "EXCHANGE_API_KEY": "",
                        "EXCHANGE_SECRET_KEY": "",
                    },
                    clear=False,
                ):
                    for name in (
                        "TRADING_MODE",
                        "EXCHANGE_ID",
                        "EXCHANGE_API_KEY",
                        "EXCHANGE_SECRET_KEY",
                    ):
                        os.environ.pop(name, None)

                    live.load_runtime_env(env_path)

                    self.assertEqual(os.environ["TRADING_MODE"], "paper")
                    self.assertEqual(os.environ["EXCHANGE_ID"], "bybit")
                    self.assertEqual(os.environ["EXCHANGE_API_KEY"], "dotenv_key")
                    self.assertEqual(
                        os.environ["EXCHANGE_SECRET_KEY"],
                        "dotenv_secret",
                    )
            finally:
                os.chdir(old_cwd)

    def test_main_loads_runtime_env_before_starting_live_runner(self):
        with patch.object(sys, "argv", ["start"]), patch.object(
            live,
            "load_runtime_env",
        ) as load_runtime_env, patch.object(live, "run_live") as run_live:
            live.main()

        load_runtime_env.assert_called_once_with()
        run_live.assert_called_once_with(
            settings_path=None,
            instrument_name=None,
            strategy_name=None,
        )


if __name__ == "__main__":
    unittest.main()
