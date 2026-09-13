#!/bin/sh
# cron 파이프라인 작업을 감싸는 래퍼: (1) 출력에 타임스탬프를 붙여 로그 파일에 남기고,
# (2) 종료 코드가 0이 아니면(OOM kill 등으로 조용히 죽는 경우 포함) notify_failure 커맨드로
# 관리자에게 이메일 알림을 보낸다. 파이프(`cmd | ts_log.sh`) 방식은 cron 기본 셸(dash)에서
# 파이프라인 마지막 명령의 종료 코드만 보이므로, 실제 종료 코드를 잡으려면 먼저 임시 파일에
# 받아둬야 한다.
#
# Usage: run_job.sh <logfile> <command> [args...]
logfile="$1"
shift

tmpfile=$(mktemp)
"$@" > "$tmpfile" 2>&1
ec=$?

while IFS= read -r line; do
    printf '%s %s\n' "$(date '+%F %T')" "$line"
done < "$tmpfile" >> "$logfile"

if [ "$ec" -ne 0 ]; then
    /home/ubuntu/nextfinup/venv/bin/python /home/ubuntu/nextfinup/manage.py notify_failure \
        --job "$(basename "$logfile" .log)" --exit-code "$ec" --log-tail "$tmpfile" \
        >> "$logfile" 2>&1
fi

rm -f "$tmpfile"
exit "$ec"
