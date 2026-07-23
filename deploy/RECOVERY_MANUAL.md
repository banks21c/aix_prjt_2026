# nextfinup.com 장애 복구 매뉴얼

시스템(OS) 업그레이드/재부팅 후 https://www.nextfinup.com/ 접속이 안 될 때 사용하는 점검 순서.
2026-07-23 장애 당시 실제로 수행했던 점검/복구 절차를 그대로 정리한 것.

구조: **인터넷 → nginx (80/443) → gunicorn/Django (127.0.0.1:9000) → MySQL (127.0.0.1:3306)**

장애가 나면 항상 이 순서(바깥 → 안쪽)로 점검한다.

---

## 0. 빠른 확인 (전체 상태 한눈에)

```bash
sudo systemctl status nginx --no-pager
sudo systemctl status nextfinup.service --no-pager
sudo systemctl status mysql --no-pager
```

셋 다 `active (running)`이면 정상. 하나라도 `failed`면 아래 해당 단계로.

---

## 1. nginx 점검

```bash
sudo nginx -t                                    # 설정 문법/인증서 로드 확인 (반드시 sudo로 실행할 것)
sudo ss -tlnp | grep -E ':80|:443'                # 80/443 리스닝 확인
```

- `nginx -t`를 sudo 없이 실행하면 `/etc/letsencrypt/...` 인증서 파일 Permission denied가 뜨는데, 이건 **정상**이다 (letsencrypt 디렉토리는 root만 읽을 수 있음). sudo로 실행했을 때만 진짜 에러로 판단한다.
- 설정 파일 위치: `/etc/nginx/sites-available/nextfinup` (심볼릭 링크: `sites-enabled/nextfinup`)
- `/` 요청은 `proxy_pass http://127.0.0.1:9000;`으로 백엔드에 전달됨.
- 문제 있으면: `sudo systemctl restart nginx` 후 다시 `nginx -t`.

nginx가 정상인데도 사이트가 안 뜨면 → 백엔드(2단계) 문제일 확률이 높음 (502/연결거부).

---

## 2. 백엔드(gunicorn/Django, `nextfinup.service`) 점검

```bash
sudo ss -tlnp | grep 9000                         # 9000번 포트에 뭔가 떠 있는지
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:9000/
sudo systemctl status nextfinup.service --no-pager -l
sudo journalctl -u nextfinup.service --no-pager -n 80
```

**9000번 포트에 아무것도 안 떠 있으면** → 서비스가 죽은 것. journalctl 로그에서 실제 에러 확인.

### 2-1. 흔한 원인: OS 업그레이드로 인한 venv 깨짐

로그에 아래와 같은 에러가 보이면 (2026-07-23 장애의 원인):

```
ModuleNotFoundError: No module named 'gunicorn'
```

**원인**: OS 릴리스 업그레이드 시 시스템 기본 `python3` 버전이 바뀌면서(예: 3.10 → 3.12), venv가 심볼릭 링크(`venv/bin/python3 -> /usr/bin/python3`)로 시스템 python을 참조하던 것이 새 버전을 가리키게 됨. 하지만 실제 설치된 패키지는 예전 버전 경로(`venv/lib/python3.10/site-packages`)에만 있어서 새 python이 못 찾음.

**확인 방법**:
```bash
/home/ubuntu/nextfinup/venv/bin/python3 --version   # venv가 가리키는 실제 버전
cat /home/ubuntu/nextfinup/venv/pyvenv.cfg           # venv 생성 당시 버전
ls /home/ubuntu/nextfinup/venv/lib/                  # site-packages가 있는 python 버전 폴더
python3 --version                                     # 현재 시스템 기본 python3
```
버전이 서로 다르면 venv가 깨진 것이 확정.

**복구 (자동 스크립트 사용, 권장)**:
```bash
/home/ubuntu/nextfinup/deploy/rebuild_venv.sh
```
- venv가 실제로 깨졌는지(python 버전 불일치, gunicorn/django import 실패) 먼저 자동 판단하고, 깨졌을 때만 재생성함 (멀쩡하면 아무것도 안 하고 종료).
- 기존 venv는 삭제하지 않고 `venv.bak.<타임스탬프>`로 백업 후, 현재 시스템 python3로 새로 만들고 `requirements.txt` 재설치, `manage.py check` 실행, 서비스 재시작 및 9000번 포트/로컬 curl까지 자동으로 확인해줌.
- 옵션:
  - `--check-only` : 재생성 없이 venv가 깨졌는지만 확인 (깨졌으면 exit code 2)
  - `--force` : 멀쩡해 보여도 강제로 재생성
  - `--no-restart` : venv만 재생성하고 서비스는 재시작하지 않음

**수동으로 할 경우** (스크립트가 없거나 직접 하고 싶을 때):
```bash
cd /home/ubuntu/nextfinup
mv venv "venv.bak.$(date +%Y%m%d%H%M%S)"          # 기존 venv는 지우지 말고 백업
python3 -m venv venv                               # 현재 시스템 python3로 새로 생성
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt                    # 시간 꽤 걸림 (playwright, scikit-learn 등 큰 패키지 포함) — 2분 넘게 걸리면 백그라운드(&)로 돌리고 로그 파일로 확인할 것
python manage.py check                             # 정상이면 "System check identified no issues" 출력
```

**서비스 재기동** (스크립트를 안 쓴 경우 수동으로):
```bash
sudo systemctl reset-failed nextfinup.service       # 재시작 실패 카운터 초기화 (안하면 "Start request repeated too quickly"로 안 뜰 수 있음)
sudo systemctl restart nextfinup.service
sleep 3
sudo systemctl status nextfinup.service --no-pager -l
sudo ss -tlnp | grep 9000
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:9000/
```

문제없이 며칠간 안정적으로 돌면 `venv.bak.*` 백업 폴더는 지워도 됨.

### 2-2. 그 외 흔한 원인들 (참고)
- `.env` 파일 누락/권한 문제 → DB 접속 정보 등이 안 읽혀서 앱이 죽을 수 있음. `/home/ubuntu/nextfinup/.env` 존재 및 권한 확인.
- 디스크 풀 → `df -h /home`으로 여유공간 확인.
- OOM(메모리 부족)으로 워커가 SIGKILL 당하는 경우 → `journalctl -u nextfinup.service`에 "was sent SIGKILL! Perhaps out of memory?" 로그가 남음. `free -h`로 메모리 확인.

---

## 3. MySQL 점검

```bash
sudo systemctl status mysql --no-pager
sudo ss -tlnp | grep 3306
```

`active (running)`이고 3306이 리스닝 중이면 정상. 죽어있으면:
```bash
sudo systemctl restart mysql
sudo journalctl -u mysql --no-pager -n 50
```

---

## 4. 최종 확인 (전체 경로 관통 테스트)

```bash
curl -sk -o /dev/null -w "HTTP %{http_code}\n" https://127.0.0.1/ -H "Host: www.nextfinup.com"   # nginx까지
curl -s  -o /dev/null -w "HTTP %{http_code}\n" https://www.nextfinup.com/ --max-time 15            # 실제 외부 접속
curl -s  -o /dev/null -w "HTTP %{http_code}\n" https://nextfinup.com/     --max-time 15
```

모두 `HTTP 200`이면 복구 완료.

---

## 참고: 재부팅 시 자동 기동 여부 확인

```bash
sudo systemctl is-enabled nginx nextfinup.service mysql
```

셋 다 `enabled`가 정상. 하나라도 아니면 `sudo systemctl enable <서비스명>`으로 등록.

## 향후 재발 방지 (검토 필요 항목)

- OS 업그레이드할 때마다 python 버전이 바뀌면 venv가 또 깨질 수 있음. 업그레이드 후에는 항상 1~2번 점검을 먼저 돌려볼 것. `deploy/rebuild_venv.sh --check-only`로 바로 확인 가능.
- venv 재생성 자동화 스크립트는 `deploy/rebuild_venv.sh`로 이미 만들어둠 (2단계 참고).
