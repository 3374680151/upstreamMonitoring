import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import app


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"success": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


class HttpProxyRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever,
            daemon=True,
        )
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/health"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    def test_request_ignores_application_proxy_and_uses_os_network_route(self):
        proxy_variables = {
            "http_proxy": "http://127.0.0.1:1",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "no_proxy": "",
            "NO_PROXY": "",
        }
        with patch.dict(os.environ, proxy_variables, clear=False):
            ok, payload, error = app.request_json(self.url)

        self.assertTrue(ok, error)
        self.assertEqual(payload, {"success": True})


if __name__ == "__main__":
    unittest.main()
