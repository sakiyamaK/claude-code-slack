#!/usr/bin/env bash
# relay を起動する。スリープ抑止のため caffeinate 経由。
#   -i: アイドルによるシステムスリープを抑止
#   -s: 電源アダプタ接続中はシステムスリープを抑止
# ※ 蓋を閉じる（クラムシェル）場合は別途 `sudo pmset -b disablesleep 1` が必要。
cd "$(dirname "$0")"
exec caffeinate -i -s ./.venv/bin/python app.py
