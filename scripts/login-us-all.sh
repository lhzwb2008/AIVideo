#!/usr/bin/env bash
# 兼容旧名 → us-credentials.sh
exec "$(cd "$(dirname "$0")" && pwd)/us-credentials.sh" "$@"
