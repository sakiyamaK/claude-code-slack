#!/usr/bin/env bash
# relay を起動する。スリープ抑止のため caffeinate 経由。
#   -i: アイドルによるシステムスリープを抑止
#   -s: 電源アダプタ接続中はシステムスリープを抑止
#
# 蓋を閉じても（クラムシェル）動かすには caffeinate だけでは足りず、
# `pmset -a disablesleep 1`（要 sudo）でクラムシェルスリープを無効化する必要がある。
# 本スクリプトは起動時に有効化し、終了時（Ctrl-C 含む）に元の設定へ戻す。
#
# 前提: 蓋を閉じて使う場合は電源アダプタを接続しておくこと。
cd "$(dirname "$0")"

CLAMSHELL_ENABLED=0

restore_clamshell() {
  if [ "$CLAMSHELL_ENABLED" = "1" ]; then
    echo "🔧 クラムシェルスリープ設定を元に戻します (disablesleep 0)"
    sudo pmset -a disablesleep 0 || true
  fi
}
trap restore_clamshell EXIT INT TERM

echo "🔧 蓋を閉じても動くようにクラムシェルスリープを無効化します (要 sudo)"
if sudo pmset -a disablesleep 1; then
  CLAMSHELL_ENABLED=1
  echo "✅ 蓋を閉じてもスリープしません（電源アダプタ接続を推奨）"
else
  echo "⚠️ disablesleep の設定に失敗しました。蓋を閉じるとスリープする可能性があります"
fi

# exec は使わない（EXIT トラップで設定を戻すため）。
# caffeinate を子プロセスで起動し、シグナルを受けたら終了トラップで復元する。
caffeinate -i -s ./.venv/bin/python app.py &
APP_PID=$!
wait "$APP_PID"
