# 도메인 구매 + EasyWP 연결 + HTTPS 설정 매뉴얼

Cloudflare에서 구매한 도메인을 Namecheap EasyWP 호스팅에 연결하고, HTTPS까지 붙이는 전 과정 기록.
`deepsleepway.com` 설정 과정(2026-08-29)을 기준으로 작성. 같은 조합(Cloudflare 도메인 + EasyWP
호스팅)을 다시 쓸 때 그대로 따라 하면 됨.

## 배경 / 왜 이 조합인가

- 도메인은 **Cloudflare**에서 구매: 원가 기준 가격에 갱신가도 동일(Namecheap/Hostinger류의
  "1년차 할인 → 이후 정가" 방식이 아님).
- 호스팅은 **Namecheap EasyWP**: 관리형 워드프레스 호스팅.
- 등록 대행사(Cloudflare)와 호스팅사(Namecheap)가 다르기 때문에, EasyWP의 **무료 자동 SSL은
  Namecheap에 등록된 도메인에서만 작동**한다는 제약이 있음. 이게 이 매뉴얼에서 가장 중요한 포인트.

## 케이스 분기: 도메인을 어디서 샀는지가 관건

아래 절차(1~8단계) 전체가 필요한 건 **도메인 등록처가 Namecheap이 아닐 때**(Cloudflare 등)뿐임.
시작 전에 먼저 확인할 것: 도메인을 Namecheap에서 샀는가, 다른 곳에서 샀는가.

- **도메인이 Cloudflare(또는 Namecheap 외 다른 곳)에 있는 경우** — 예: `deepsleepway.com`.
  EasyWP 무료 SSL이 안 먹히므로 4~7단계의 Cloudflare Origin CA 우회가 반드시 필요함.
- **도메인도 Namecheap에 있는 경우** — 예: `timelessculturelab.com`. EasyWP 대시보드에서
  도메인만 연결하면 **Let's Encrypt SSL(free)이 자동으로 발급됨**, Origin CA 인증서나
  Cloudflare 프록시 설정 등 4~7단계는 전부 건너뛰어도 됨. 2026-08-29 재확인 결과: 네임서버는
  Namecheap `registrar-servers.com` 그대로, root(A)/www(CNAME→root) 둘 다 정상 해석, 인증서는
  `CN=timelessculturelab.com` + SAN에 root/www 둘 다 포함된 Sectigo 발급 도메인 전용 인증서로
  정상 작동 확인. 손댈 것 없음.

이 매뉴얼의 1~3단계(도메인 구매, EasyWP 사이트 생성, DNS 레코드 등록)는 두 케이스 공통이고,
4단계부터가 Cloudflare 도메인 케이스 전용임.

## 1단계: 도메인 구매 (Cloudflare)

1. Cloudflare 대시보드 → Domain Registration → 원하는 도메인 검색 후 구매
2. 구매 후 Cloudflare 대시보드에 해당 도메인이 "Active" 상태로 표시되는지 확인
3. 네임서버는 Cloudflare가 자동으로 세팅됨 (별도 작업 불필요)

## 2단계: EasyWP 호스팅 구매 + 사이트 생성

1. Namecheap → EasyWP Starter(또는 상위 플랜) 구매
2. 새 워드프레스 사이트 생성 (제목만 임시로 넣어도 됨, 나중에 변경 가능)
3. EasyWP 대시보드 → 해당 사이트 → **Domain** → **Change** → 구매한 도메인 입력해서 연결 시작
   - 이 단계에서 EasyWP가 임시 도메인(`*.ewp.live`)을 커스텀 도메인으로 바꿔줌

## 3단계: Cloudflare DNS 레코드 설정

EasyWP가 도메인 연결 화면에서 알려주는 대상(보통 `ingress-<서버명>.easywp.com` 형태의 호스트명)을
Cloudflare DNS에 등록한다.

Cloudflare 대시보드 → 도메인 선택 → **DNS → Records → Add record**

| Type  | Name | Target                          | Proxy status        |
|-------|------|----------------------------------|----------------------|
| CNAME | `@`  | `ingress-xxxxx.easywp.com`      | 처음엔 DNS only(회색 구름) |
| CNAME | `www`| `ingress-xxxxx.easywp.com`      | 처음엔 DNS only(회색 구름) |

- 루트(`@`)에 CNAME을 쓰는 건 DNS 표준상 원래 불가능하지만, Cloudflare가 **CNAME flattening**으로
  자동 처리해줌 (DNS 레코드 화면에 관련 안내 아이콘이 뜨는데, 오류 아니고 정보성 안내임 — 무시해도 됨)
- **주의**: root와 www 둘 다 빠뜨리지 말고 등록. 하나만 하면 나머지 하나는 접속 안 됨(NXDOMAIN)
- 처음엔 **프록시 끔(회색 구름, DNS only)** 상태로 둔다 — 이유는 4단계 참고

## 4단계: HTTPS — 왜 EasyWP 무료 SSL이 안 되는가

EasyWP 대시보드 → 해당 사이트 → **SSL Certificate → Manage(또는 Add)** 로 들어가면 옵션이 세 개
있음:

- **No SSL Certificate** (기본값)
- **Let's Encrypt SSL (free)** — ⚠️ **"This feature is only available for Namecheap Domains"**
  라고 표시됨. 도메인을 Cloudflare에서 구매했다면 이 옵션은 처음부터 사용 불가. DNS를 아무리
  잘 맞춰도 몇 시간을 기다려도 자동 발급되지 않음 (실제로 겪은 문제, 6시간 이상 낭비함).
- **Custom SSL Certificate** ← 이걸 써야 함

## 5단계: Cloudflare Origin CA 인증서 발급

1. Cloudflare 대시보드 → 도메인 선택 → **SSL/TLS → Origin Server** 탭 → **Create Certificate**
2. 설정값:
   - Private key type: **RSA (2048)**
   - Hostnames: `example.com`, `*.example.com` (기본값 그대로 두면 root+모든 서브도메인 커버)
   - Certificate Validity: 15년(기본값) 그대로
3. **Key Format**은 세 가지 중 **PEM** 선택 (PKCS#7은 Windows/IIS용, DER은 바이너리라 텍스트
   붙여넣기 방식엔 안 맞음 — cPanel 계열인 EasyWP는 PEM이 표준)
4. 생성하면 **Certificate**와 **Private Key** 두 텍스트 블록이 뜸 — 이 창은 다시 열 수 없으니
   반드시 그 자리에서 복사해서 안전하게 보관 (비밀번호 관리자 등, 채팅이나 메모장에 평문으로
   남기지 말 것)

## 6단계: EasyWP에 Custom SSL 등록

EasyWP → SSL Certificate → Custom SSL Certificate 섹션:

- **Private Key**: 5단계에서 받은 Private Key 전체 붙여넣기
- **SSL Certificate**: 5단계에서 받은 Certificate 전체 붙여넣기
- **CA Bundle**: **비워둬도 됨**. Cloudflare Origin CA 인증서는 공인 CA 체인이 아니라
  Cloudflare 자체만 신뢰하는 방식이라(브라우저는 나중에 Cloudflare의 Universal SSL만 보게 됨),
  별도 CA 번들 없이도 정상 작동함
- 저장 후 **Status를 반드시 "Active"로 전환** — 업로드만 하고 Active 토글을 안 누르면 계속
  적용 안 된 상태로 남아있음 (실제로 이 단계를 놓쳐서 한참 헤맸음)

## 7단계: Cloudflare SSL/TLS 모드 + 프록시 켜기

1. Cloudflare → **SSL/TLS → Overview** → 암호화 모드를 **Full**로 설정
   - **Flexible은 쓰지 말 것** — 워드프레스가 HTTPS를 강제하는 상태에서 Flexible을 쓰면
     Cloudflare→origin 구간이 평문 HTTP라 리다이렉트 무한루프가 날 수 있음
2. Cloudflare → **DNS → Records** → 3단계에서 만든 root/www 레코드 둘 다 **프록시 켜기
   (주황 구름, Proxied)**로 전환
   - Origin CA 방식은 Cloudflare가 방문자에게는 자체 인증서를, origin에는 Origin CA 인증서를
     쓰는 구조라 프록시가 켜져 있어야 의미가 있음 (DNS only면 Cloudflare를 아예 안 거침)

## 8단계: 확인

- `https://example.com/`, `https://www.example.com/` 둘 다 접속해서 자물쇠 아이콘 확인
- 인증서 정보에서 발급자가 `Google Trust Services`(Cloudflare Universal SSL) 등으로 뜨면 정상
- `curl -sI https://example.com/` 로 응답 헤더에 `server: cloudflare`가 찍히면 프록시 정상 작동

## 트러블슈팅

**"주의 요함" / "이 사이트는 안전하지 않습니다" 경고가 뜨는데 서버는 이미 정상인 것 같다**

십중팔구 클라이언트 쪽 캐시 문제. 순서대로 확인:

1. **시크릿(프라이빗) 창**으로 먼저 열어보기 — 여기서 경고 없으면 서버는 100% 정상, 원래 창의
   캐시가 원인
2. OS DNS 캐시 지우기: Windows `ipconfig /flushdns`, Mac
   `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`
3. Chrome 자체 DNS 캐시(별도 존재, OS 캐시로 안 지워짐): `chrome://net-internals/#dns` →
   "Clear host cache" → 브라우저 완전 종료 후 재실행
4. 그래도 안 되면 공유기 자체 DNS 캐시 의심 — **모바일 데이터로 테스트**(완전히 다른 네트워크
   경로라 공유기 캐시를 우회함)

**DNS 레코드 화면에 "CNAME records normally can not be on the zone apex..." 느낌표가 뜬다**

오류 아님. CNAME flattening 관련 정보성 안내. 3단계 참고, 무시해도 됨.

**www만 되고 root(또는 반대)만 안 된다**

DNS 레코드가 둘 중 하나만 등록됐거나, 최근에 추가한 쪽만 로컬 캐시가 없어서 먼저 뜨는 경우가
대부분. `curl -sI` 로 서버 응답을 직접 비교해서 서버 쪽 헤더/인증서가 동일한지 먼저 확인하고,
동일하다면 트러블슈팅 섹션의 캐시 문제로 접근.

## 보안 참고사항

- Origin CA 인증서의 Private Key는 절대 채팅/메모장 등 평문으로 남기지 말 것. 실수로 노출됐다면
  Cloudflare → SSL/TLS → Origin Server에서 기존 인증서를 **Revoke**하고 새로 발급 → EasyWP에
  재업로드 (DNS 재설정은 필요 없음, 인증서/키만 교체하면 됨)
- Origin CA 인증서는 공인 CA 체인이 아니라 Cloudflare 자체만 신뢰하는 방식이라, 키가 유출돼도
  일반 브라우저를 대상으로 한 위장(공인 인증서처럼 보이게 하는 것)은 불가능함 — 다만 원칙적으로는
  유출된 키는 재발급하는 게 안전함
