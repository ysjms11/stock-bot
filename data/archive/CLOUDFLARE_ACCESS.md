# Cloudflare Access 대시보드 보안 가이드

> bot.arcbot-server.org 대시보드에 Cloudflare Zero Trust Access 로 로그인 게이트 추가.
> 소요 시간: 약 15~20분 (콘솔 작업).

---

## 왜 필요한가?

현재 `https://bot.arcbot-server.org/dash/*` 가 **인증 없이 외부 노출**됨. 포트폴리오, 투자 판단, thesis 문서가 URL 만 알면 누구나 접근 가능.

Cloudflare Zero Trust Access 로 추가하면:
- 본인 Google 계정(`arcturusnd@gmail.com`)으로만 접근
- 다른 사람이 URL 알아도 로그인 화면만 보임
- 코드 변경 거의 없음 (Cloudflare Tunnel 이미 사용 중이라 Access 가 자연스럽게 얹힘)

---

## 준비물

- Cloudflare 계정 (이미 `arcbot-server.org` 도메인 등록됨)
- Google 계정 (arcturusnd@gmail.com)
- 브라우저

---

## 단계별 작업

### 1. Cloudflare Zero Trust 활성화 (아직 안 했으면)

1. https://dash.cloudflare.com 로그인
2. 왼쪽 메뉴 **Zero Trust** 클릭
3. 팀 이름 설정 (예: `kreuzer-home`) — 한 번만. 무료 플랜 (50명까지) 선택
4. 완료되면 `https://<팀이름>.cloudflareaccess.com` 이 프로젝트 URL 됨

### 2. Identity Provider 추가 (Google 로그인)

1. Zero Trust > **Settings** > **Authentication** > **Login methods**
2. **Add new** > **Google** 선택
3. Google Cloud Console 에서 OAuth 2.0 Client ID 발급 가이드 따라감 — Cloudflare 가 안내 링크 제공
4. Client ID / Secret 받아서 입력 + **Save**
5. **Test** 버튼으로 본인 Google 계정 로그인 테스트

> **간편 대안**: Google OAuth 설정 번거로우면 **One-time PIN** 방식도 됨. 이메일로 6자리 PIN 보내서 로그인. 설정 5분이면 끝.

### 3. Application 등록 (대시보드 보호)

1. Zero Trust > **Access** > **Applications** > **Add an application**
2. **Self-hosted** 선택
3. 다음 정보 입력:
   - **Application name**: `stock-bot dashboard`
   - **Session Duration**: `24 hours` (하루 1번 로그인)
   - **Application domain**:
     - Subdomain: `bot`
     - Domain: `arcbot-server.org`
     - Path: `/dash` (또는 `/dash/*`)

   → 정확하게 `/dash` 로만 잡으면 `/mcp`, `/health` 같은 다른 경로는 인증 없음 (MCP 클라이언트/Claude.ai 접근 유지). 원하는 범위 확인.

4. **Next** 클릭

### 4. Policy 설정 (누가 접근 가능?)

1. **Policy name**: `owner-only`
2. **Action**: `Allow`
3. **Configure rules**:
   - **Include** > **Emails** > `arcturusnd@gmail.com`

   (혹은 **Emails ending in** `@gmail.com` 이면 특정 이메일만 가능)

4. **Next** > **Add application**

### 5. 테스트

1. 시크릿 모드 브라우저에서 `https://bot.arcbot-server.org/dash/` 접속
2. Cloudflare 로그인 화면 → Google (또는 PIN) 로그인
3. 로그인 성공 시 대시보드 접근 OK
4. 다른 이메일/구글 계정으로 시도 → 403 Forbidden 확인

---

## 설정 확인 체크리스트

- [ ] 본인은 대시보드 정상 접근 가능
- [ ] 시크릿 모드 / 다른 브라우저에서 로그인 요구됨
- [ ] `/health` 는 인증 없이 접근 가능 (헬스체크 깨지면 안 됨)
- [ ] `/mcp`, `/mcp/messages` 도 인증 없이 접근 가능 (Claude.ai MCP 연결 유지)
- [ ] Claude.ai MCP 연결이 끊어지지 않음 (manage_watch/get_portfolio 등 정상)

---

## 주의사항

### 경로 범위 신중히
- Path `/` 로 설정하면 MCP 엔드포인트까지 보호됨 → **Claude.ai 연결 끊김**
- 반드시 `/dash` 또는 `/dash/*` 로 좁혀서 지정

### 본인 접근 차단 방지
- Policy 저장 전에 Include 이메일 정확한지 2회 확인
- 만약 실수로 차단되면:
  1. Cloudflare 콘솔 > Zero Trust > Access > Applications
  2. 해당 Application 삭제 또는 Policy 편집
  3. 5분 내 반영

### Session Duration
- `24 hours` = 하루 1번 로그인. 적당
- 너무 짧게 (1h) 하면 자주 로그인 귀찮음
- 너무 길게 (30d) 하면 공용 컴퓨터에서 로그인한 후 로그아웃 잊으면 위험

---

## 코드 변경 필요한가?

**아니요 (기본 설정에서는).**

Cloudflare Tunnel 이 이미 앞단에서 받고 있어서, Access 가 Tunnel 앞에 자동으로 들어감. 백엔드 (맥미니 main.py) 는 아무것도 안 건드려도 됨.

**옵션**: JWT 검증 추가하면 백엔드에서도 재검증 가능 (이중 방어). 하지만 기본 설정만으로 충분 (Cloudflare Access 가 로그인 없으면 아예 원본 서버로 요청 안 보냄).

---

## 참고 링크

- [Cloudflare Zero Trust Applications 가이드](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-apps/)
- [Google 로그인 설정](https://developers.cloudflare.com/cloudflare-one/identity/idp-integration/google/)
- [One-time PIN 대안](https://developers.cloudflare.com/cloudflare-one/identity/one-time-pin/)

---

## 완료 후 보고

콘솔 작업 완료하면 데이터에 기록:

```
# PROGRESS.md 아키텍처 결정 표에 추가
| 2026-04-23 | 대시보드 Cloudflare Access 적용 | /dash/* 만 보호, MCP/health 는 무인증 유지. Google (또는 PIN) 로그인, 세션 24h |
```

→ 이후 `data/TODO_dev.md` 에서 **P1 대시보드 인증** 항목 체크.
