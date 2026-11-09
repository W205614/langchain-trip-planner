#!/bin/sh
set -eu
key=${AMAP_WEB_JS_KEY:-}
case "$key" in
  *[!a-zA-Z0-9_-]*) echo 'AMAP_WEB_JS_KEY must contain only letters, digits, underscores or hyphens' >&2; exit 1 ;;
esac
printf 'window.__TRIP_CONFIG__ = {"amapWebJsKey":"%s"};\n' "$key" > /usr/share/nginx/html/runtime-config.js
