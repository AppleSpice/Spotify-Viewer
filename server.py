import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
GENIUS_API = "https://api.genius.com"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024


class ViewerHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/genius-api/"):
            self.proxy_genius(parsed)
            return
        super().do_GET()

    def proxy_genius(self, parsed):
        if parsed.path == "/genius-api/search":
            endpoint = "/search"
        else:
            song_match = re.fullmatch(r"/genius-api/songs/([0-9]+)", parsed.path)
            if not song_match:
                self.send_error(404, "Unknown Genius API route")
                return
            endpoint = f"/songs/{song_match.group(1)}"

        query = parse_qs(parsed.query, keep_blank_values=True)
        if endpoint == "/search":
            search_term = query.get("q", [""])[0].strip()
            if not search_term:
                self.send_error(400, "A Genius search term is required")
                return
            query_string = urlencode({"q": search_term})
        else:
            query_string = urlencode({"text_format": "plain"})

        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) <= len("Bearer "):
            self.send_error(401, "A Genius Client Access Token is required")
            return

        request = Request(
            f"{GENIUS_API}{endpoint}?{query_string}",
            headers={
                "Accept": "application/json",
                "Authorization": authorization,
                "User-Agent": "SpotifyViewer/1.0",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                self.send_upstream_response(response.status, response.read(MAX_RESPONSE_BYTES + 1))
        except HTTPError as error:
            self.send_upstream_response(error.code, error.read(MAX_RESPONSE_BYTES + 1))
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            self.send_json(
                502,
                {"meta": {"message": f"The local server could not reach Genius: {reason}"}},
            )

    def send_upstream_response(self, status, body):
        if len(body) > MAX_RESPONSE_BYTES:
            self.send_json(502, {"meta": {"message": "Genius returned an unexpectedly large response."}})
            return
        self.send_json(status, body)

    def send_json(self, status, body):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        # Do not log request headers, which include the Genius access token.
        super().log_message(format_string, *args)


if __name__ == "__main__":
    address = ("127.0.0.1", 8000)
    server = ThreadingHTTPServer(address, ViewerHandler)
    print(f"Spotify Viewer is available at http://{address[0]}:{address[1]}/")
    print("Keep this window open while using the viewer. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Spotify Viewer server.")
    finally:
        server.server_close()
