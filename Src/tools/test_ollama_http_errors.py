from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from urllib import error as urlerror
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

from norm_runtime.ollama_client import OllamaClient


class OllamaHttpErrorTests(unittest.TestCase):
    @staticmethod
    def _http_400(body: bytes) -> urlerror.HTTPError:
        return urlerror.HTTPError(
            url="http://127.0.0.1:11434/api/chat",
            code=400,
            msg="Bad Request",
            hdrs={"Content-Type": "application/json"},
            fp=io.BytesIO(body),
        )

    @patch("norm_runtime.ollama_client.request.urlopen")
    def test_chat_http_error_retains_ollama_response_body(self, urlopen):
        urlopen.side_effect = self._http_400(b'{"error":"tool schema rejected"}')
        client = OllamaClient("http://127.0.0.1:11434", model="granite4.2:30b")

        with self.assertLogs(level="ERROR") as captured:
            with self.assertRaisesRegex(RuntimeError, "tool schema rejected"):
                client.chat_with_tools(
                    [{"role": "user", "content": "inspect"}],
                    [{
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "description": "read one file",
                            "parameters": {
                                "type": "object",
                                "properties": {"path": {"type": "string"}},
                                "required": ["path"],
                            },
                        },
                    }],
                    think=True,
                    num_predict=64,
                )

        joined = "\n".join(captured.output)
        self.assertIn("endpoint=/api/chat", joined)
        self.assertIn("model=granite4.2:30b", joined)
        self.assertIn("status=400", joined)
        self.assertIn("tool schema rejected", joined)
        self.assertIn('"tool_count": 1', joined)
        self.assertIn('"message_count": 1', joined)
        self.assertNotIn('"content": "inspect"', joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
