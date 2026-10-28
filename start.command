#!/bin/sh
cd "$(dirname "$0")"
exec python3 -m backend.app --port 8765
