#!/usr/bin/env bash
# relay を起動する。スリープ抑止のため caffeinate 経由。
cd "$(dirname "$0")"
exec caffeinate ./.venv/bin/python app.py
