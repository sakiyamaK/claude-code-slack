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

# sudo 付きで起動されると app.py も claude も root で動き、HOME=/var/root になって
# ユーザーの ~/.claude 認証が見えず「Not logged in · Please run /login」になる。
# pmset に必要な sudo はスクリプト内部で個別に呼ぶので、全体を sudo する必要はない。
if [ "$(id -u)" = "0" ]; then
  echo "❌ sudo を付けて実行しないでください（sudo bash run.sh ではなく bash run.sh）。"
  echo "   root で動かすと claude が ~/.claude の認証を見つけられず"
  echo "   Slack の返答が「Not logged in · Please run /login」になります。"
  echo "   pmset に必要な sudo はスクリプト内部で聞きます。"
  exit 1
fi

CLAMSHELL_ENABLED=0

restore_clamshell() {
  if [ "$CLAMSHELL_ENABLED" = "1" ]; then
    echo "🔧 クラムシェルスリープ設定を元に戻します (disablesleep 0)"
    sudo pmset -a disablesleep 0 || true
  fi
}
# 終了時は子プロセスも確実に落とす。
# これが無いと、親の bash だけ死んだとき python が孤児（PPID=1）として生き残り、
# 次回起動と多重に Slack へ接続してしまう。古い config.yml を読んだままの
# インスタンスが混ざるため、設定変更が反映されない原因になる。
cleanup() {
  if [ -n "$APP_PID" ] && kill -0 "$APP_PID" 2>/dev/null; then
    pkill -TERM -P "$APP_PID" 2>/dev/null || true   # caffeinate 配下の python
    kill  -TERM    "$APP_PID" 2>/dev/null || true
    for _ in 1 2 3 4 5; do                          # 最大5秒だけ穏当に待つ
      kill -0 "$APP_PID" 2>/dev/null || break
      sleep 1
    done
    pkill -KILL -P "$APP_PID" 2>/dev/null || true   # 残っていれば強制終了
    kill  -KILL    "$APP_PID" 2>/dev/null || true
  fi
  restore_clamshell
}
trap cleanup EXIT INT TERM

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
