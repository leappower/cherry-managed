#!/bin/bash
# Mac 端口转发脚本 — 临时方案
# 让局域网其他电脑访问本机 CherryStudio API
# 用法：bash mac-lan-socat.sh

SOCAT=$(which socat 2>/dev/null)
if [ -z "$SOCAT" ]; then
    echo "需要安装 socat: brew install socat"
    exit 1
fi

echo "启动端口转发: 0.0.0.0:23333 → 127.0.0.1:23333"
$SOCAT TCP-LISTEN:23333,fork TCP:127.0.0.1:23333 &
echo "PID: $!"
echo "停止: kill $!"
