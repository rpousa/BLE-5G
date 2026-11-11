#!/usr/bin/env python3
"""Serves the published pages (docs/pages of the BLE-5G repository) on the LAN.

The pages are written as artifact bodies: no <!doctype>, charset or viewport, which the artifact host adds.
This server adds that skeleton to any .html file that lacks a doctype, and sends text/html; charset=utf-8,
so the pages render the same as on claude.ai. Everything else is served as is.

  python3 pages_server.py [--port 8088] [--dir /home/debian/BLE-5G/docs/pages]
"""
import argparse
import functools
import http.server
import os

HEAD = (b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        b'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>\n')


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                      ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8"}

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            index = os.path.join(path, "index.html")
            if not os.path.exists(index):
                return super().send_head()          # directory listing
            path = index
        if not path.endswith(".html") or not os.path.isfile(path):
            return super().send_head()
        with open(path, "rb") as f:
            body = f.read()
        if not body.lstrip()[:15].lower().startswith(b"<!doctype"):
            body = HEAD + body + b"\n</body></html>\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        import io
        return io.BytesIO(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--dir", default="/home/debian/BLE-5G/docs/pages")
    a = ap.parse_args()
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", a.port), functools.partial(Handler, directory=a.dir))
    print(f"serving {a.dir} on :{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
