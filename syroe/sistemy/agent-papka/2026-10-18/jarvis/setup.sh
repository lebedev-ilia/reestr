#!/usr/bin/env bash
# Установка окружения. Запускать из папки jarvis:  ./setup.sh
set -euo pipefail

cd "$(dirname "$0")"
PY=${PYTHON:-python3}

echo "→ проверяю python"
$PY -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null || {
  echo "  нужен Python 3.11+, найден: $($PY --version 2>&1)"
  echo "  укажи другой: PYTHON=python3.12 ./setup.sh"
  exit 1
}
echo "  $($PY --version)"

echo "→ создаю .venv"
[ -d .venv ] || $PY -m venv .venv
./.venv/bin/python -m pip install --upgrade pip -q

echo "→ ставлю зависимости"
./.venv/bin/pip install -r requirements.txt -q
echo "  claude-agent-sdk $(./.venv/bin/pip show claude-agent-sdk | awk '/^Version/{print $2}')"

echo "→ проверяю Claude Code"
if command -v claude >/dev/null 2>&1; then
  echo "  $(claude --version 2>&1 | head -1)"
else
  echo "  не найден. Поставь и залогинься по подписке:"
  echo "    npm install -g @anthropic-ai/claude-code"
  echo "    claude login"
fi

echo "→ проверяю сборку"
./.venv/bin/python -c "
import sys; sys.path.insert(0, '..')
from jarvis.core import agent
o = agent.build_options(fast=False, unattended=True)
print(f'  контекст собирается: {len(o.system_prompt)} символов, инструментов {len(o.allowed_tools)}')
"

cat <<'EOF'

Готово. Запуск:

  ./jarvis                  диалог
  ./jarvis "вопрос"         один ход
  ./jarvis --night          ночной цикл вручную
  ./jarvis --context        что уходит в контекст

Ночной цикл сам живёт внутри демона ВК (core/night.py) и догоняет пропущенное —
крон не нужен. Проверить расписание: python -m jarvis.core.night
EOF
echo
