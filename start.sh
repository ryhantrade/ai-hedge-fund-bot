#!/bin/bash
# Entry point for the Render web service: run the board from the repo root.
set -e
cd "$(dirname "$0")"
exec python board.py
