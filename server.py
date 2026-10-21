"""Run Scoutly locally: python server.py [port]   (default 8000)

The server code lives in seo/engine.py; this file only starts it from the project folder.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "seo"))
import engine  # noqa: E402

engine.main()
