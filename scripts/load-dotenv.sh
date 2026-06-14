#!/usr/bin/env bash
# 从单个 .env 按分块加载环境变量
# 文件用 "#== section: shared|zh|en ==" 行分块：
#   shared 永远加载（setdefault）；匹配 locale 的分块加载并覆盖；其他分块跳过
# 用法: source scripts/load-dotenv.sh zh|en
set -euo pipefail

_locale="${1:-zh}"
if [[ "$_locale" != "zh" && "$_locale" != "en" ]]; then
  echo "load-dotenv: locale 须为 zh 或 en，收到: $_locale" >&2
  return 1 2>/dev/null || exit 1
fi

_apply_line() {
  local line="$1" force="$2"
  line="${line%%$'\r'}"
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && return 0
  [[ "$line" != *"="* ]] && return 0
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
}

_load_env() {
  local file="$1" want_locale="$2"
  [[ -f "$file" ]] || return 0
  local section="shared"
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^#==[[:space:]]*section:[[:space:]]*([a-zA-Z]+)[[:space:]]*== ]]; then
      section="${BASH_REMATCH[1]}"
      continue
    fi
    case "$section" in
      shared) _apply_line "$line" 0 ;;
      "$want_locale") _apply_line "$line" 1 ;;
      *) : ;;  # 其他语言分块跳过
    esac
  done < "$file"
}

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# 切换 locale 时清掉另一套流水线的品牌/尾页变量，避免同 shell 先跑 en 再跑 zh 时残留英文
for _k in AIVIDEO_BRAND_NAME AIVIDEO_BRAND_TAGLINE \
  AIVIDEO_OUTRO_HEADLINE AIVIDEO_OUTRO_SUBLINE \
  AIVIDEO_OUTRO_NARRATION AIVIDEO_OUTRO_NARRATION_VARIANTS; do
  unset "$_k" 2>/dev/null || true
done
_load_env "$ROOT/.env" "$_locale"
export AIVIDEO_LOCALE="$_locale"
