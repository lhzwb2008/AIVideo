#!/usr/bin/env bash
# 分层加载环境变量：.env（共享密钥，setdefault）+ .env.{zh|en}（语言专属，覆盖）
# 用法: source scripts/load-dotenv.sh zh|en
set -euo pipefail

_locale="${1:-zh}"
if [[ "$_locale" != "zh" && "$_locale" != "en" ]]; then
  echo "load-dotenv: locale 须为 zh 或 en，收到: $_locale" >&2
  return 1 2>/dev/null || exit 1
fi

_load_file() {
  local file="$1"
  local force="${2:-0}"
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *"="* ]] && continue
    local key="${line%%=*}"
    key="${key//[[:space:]]/}"
    local val="${line#*=}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "$val" =~ ^\".*\"$ ]]; then val="${val:1:${#val}-2}"; fi
    if [[ "$val" =~ ^\'.*\'$ ]]; then val="${val:1:${#val}-2}"; fi
    if [[ "$force" == "1" || -z "${!key+x}" ]]; then
      export "$key=$val"
    fi
  done < "$file"
}

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_load_file "$ROOT/.env" 0
_load_file "$ROOT/.env.$_locale" 1
export AIVIDEO_LOCALE="$_locale"
