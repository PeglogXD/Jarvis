"""
J.A.R.V.I.S. — Proxy local para NVIDIA NIM API (Streaming)
Permite que JARVIS.html llame a NVIDIA NIM desde el navegador (CORS bypass).

Uso:
  python proxy.py          (inicia en http://localhost:5050)
  Abre JARVIS.html en el navegador
"""
import http.server
import json
import os
import urllib.request
import urllib.error
import ssl

PORT = 5050
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
API_KEY = os.getenv("NVIDIA_API_KEY", "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")

# Skip SSL verification issues on some systems
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)

            is_stream = data.get("stream", False)

            req = urllib.request.Request(
                NVIDIA_URL,
                data=json.dumps(data).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_KEY}",
                },
                method="POST",
            )

            resp = urllib.request.urlopen(req, timeout=120, context=ctx)

            if is_stream:
                # Streaming: forward SSE chunks directly
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                for chunk in iter(lambda: resp.read(1024), b""):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                # Non-streaming: forward JSON response
                result = json.loads(resp.read().decode())
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        except urllib.error.HTTPError as e:
            err_body = e.read().decode() if e.fp else str(e)
            self.send_response(e.code)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": err_body}).encode())

        except Exception as e:
            self.send_response(500)
            self._cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def log_message(self, format, *args):
        print(f"[PROXY] {args[0]}")


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", PORT), ProxyHandler)
    print(f"J.A.R.V.I.S. Proxy activo en http://localhost:{PORT}")
    print(f"Streaming: ACTIVADO")
    print(f"Presiona Ctrl+C para detener.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy detenido.")
        server.server_close()
