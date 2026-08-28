#!/usr/bin/env python3
"""Serve o PWA localmente: python scripts/serve_pwa.py 5173"""
import functools
import http.server
import os
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
root = os.path.join(os.path.dirname(__file__), "..", "frontend")
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
print(f"http://localhost:{port}")
http.server.ThreadingHTTPServer(("", port), handler).serve_forever()
