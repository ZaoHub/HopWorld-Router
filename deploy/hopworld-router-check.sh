#!/bin/bash
# Print management access and run read-only checks for the systemd service.
set -eu

unit=hopworld-router.service
env_file=/etc/hopworld-router.env
mode=${1:-}
case "$mode" in
    ''|--instructions) ;;
    *) printf '用法：%s [--instructions]\n' "$0" >&2; exit 2 ;;
esac

pid=$(systemctl show -p MainPID "$unit" 2>/dev/null || true)
pid=${pid#MainPID=}

setting() {
    local name=$1 entry
    if [[ "$pid" =~ ^[1-9][0-9]*$ && -r "/proc/$pid/environ" ]]; then
        while IFS= read -r -d '' entry; do
            if [[ "$entry" == "$name="* ]]; then
                printf '%s\n' "${entry#*=}"
                break
            fi
        done < "/proc/$pid/environ"
    elif [[ -r "$env_file" ]]; then
        awk -v key="$name=" '
            index($0, key) == 1 {
                value = substr($0, length(key) + 1)
                sub(/^[[:space:]]+/, "", value)
                sub(/[[:space:]]+$/, "", value)
                if (value ~ /^".*"$/ || value ~ /^\047.*\047$/)
                    value = substr(value, 2, length(value) - 2)
            }
            END {if (value != "") print value}
        ' "$env_file"
    fi
}

port=$(setting HOPWORLD_PORT)
if ! [[ "$port" =~ ^[0-9]{1,5}$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then port=8080; fi
port=$((10#$port))
host=$(setting HOPWORLD_HOST)
if ! [[ "$host" =~ ^[a-zA-Z0-9._:-]*$ ]]; then host=; fi
case "$host" in
    ''|0.0.0.0) host=127.0.0.1 ;;
    ::) host=::1 ;;
esac
case "$host" in
    *:*) host="[$host]" ;;
esac
url="http://$host:$port"
https_host=$(setting HOPWORLD_MANAGEMENT_HTTPS_HOST)

printf '\n%s\n' 'HopWorld Router 管理与排障'
if [[ "$https_host" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]; then
    printf '已配置的公网管理地址： https://%s/ （TCP 443）\n' "$https_host"
fi
printf '服务器基础管理地址： %s/ （切换过管理端口时用于恢复）\n' "$url"
printf '在自己的电脑建立 SSH 隧道： ssh -N -L 18080:%s:%s 用户名@服务器地址\n' "$host" "$port"
printf '%s\n' '隧道连接后打开：http://127.0.0.1:18080/ 。SSH 使用自定义端口时，在命令中加 -p 对应端口。'
printf '%s\n' '新安装默认账号：admin' '新安装默认密码：admin' '首次通过本机或 SSH 隧道打开时，请设置至少 6 个字符的新密码。' \
    '' '网页打不开时，在服务器终端执行：' \
    '  查看服务： sudo systemctl status hopworld-router.service --no-pager -l' \
    '  查看日志： sudo journalctl -u hopworld-router.service -n 80 --no-pager' \
    '  启动服务： sudo systemctl start hopworld-router.service' \
    '  停止服务： sudo systemctl stop hopworld-router.service' \
    '  重启服务： sudo systemctl restart hopworld-router.service'
printf '  检查就绪： curl --noproxy "*" -fsS --max-time 5 %s/health/ready\n' "$url"
printf '%s\n' '  找回管理入口： sudoedit /etc/hopworld-router.env' \
    '    若曾在后台切换管理端口，设置 HOPWORLD_MANAGEMENT_RECOVERY=1 后重启，通过基础地址重新设置入口。' \
    '    恢复后移除此设置并重启。'
if [[ "$https_host" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]] && command -v nginx >/dev/null 2>&1; then
    printf '%s\n' '  检查 HTTPS 反代： sudo nginx -t' '  查看反代状态： sudo systemctl status nginx --no-pager'
fi
if command -v hopworld-router-uninstall >/dev/null 2>&1; then
    printf '%s\n' '' '完整卸载： sudo hopworld-router-uninstall'
elif [[ "$(systemctl show -p WorkingDirectory "$unit" 2>/dev/null || true)" == 'WorkingDirectory=/opt/hopworld-router' ]]; then
    printf '%s\n' '' '完整卸载： cd /opt/hopworld-router && sudo .venv/bin/python -m router.uninstall'
fi

if [[ -z "$mode" ]]; then
    printf '\n%s\n' '当前服务状态'
    systemctl status "$unit" --no-pager -l || true
    printf '\n%s\n' '本机就绪检查'
    curl --noproxy '*' -fsS --max-time 5 "$url/health/ready" || printf '%s' '未就绪，请检查上述服务状态和日志。'
    printf '\n'
fi
