#!/usr/bin/env bash
# 初回セットアップ: venv 作成 → 依存インストール → config.yml 用意。
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "python3 がありません"; exit 1; }
command -v claude  >/dev/null || echo "⚠️ claude CLI が見つかりません（ログイン済みの claude が必要）"

[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt

if [ ! -f config.yml ]; then
  cp config.sample.yml config.yml
  echo "config.yml を作成しました。"
fi

echo "✅ セットアップ完了。"
echo "→ config.yml に paths.target_repo と Slack トークン(xoxb/xapp)・自分のメンバーIDを記入"
echo "→ ./run.sh で起動"
