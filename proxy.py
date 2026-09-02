"""
J.A.R.V.I.S. — Proxy local para NVIDIA NIM API
Permite que JARVIS.html llame a NVIDIA NIM desde el navegador (CORS bypass).

Uso:
  python proxy.py          (inicia en http://localhost:5050)
  Abre JARVIS.html en el navegador

El HTML llama a http://localhost:5050/api/chat → este proxy lo reenvía a NVIDIA NIM.
"""
import http.server
import json
import os
import urllib.request
import urllib.error

PORT = 5050
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
API_KEY = os.getenv("NVIDIA_API_KEY", "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")


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        """Handle CORS preflight."""
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

            # Forward to NVIDIA NIM
            req = urllib.request.Request(
                NVIDIA_URL,
                data=json.dumps(data).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_KEY}",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=120) as resp:
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
    print(f"Abre JARVIS.html en tu navegador y conectará automáticamente.")
    print(f"Presiona Ctrl+C para detener.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy detenido.")
        server.server_close()
