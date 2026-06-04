"""
CI용 컨센서스 테스트 - main.py의 get_hankyung_consensus 실행 후 텔레그램 전송
성공 기준: consensus_target > 0 OR reports 1개 이상
"""
import asyncio
import os
import sys
import aiohttp
import pytest

# main.py의 함수를 그대로 임포트 (telegram 패키지 없이 동작하도록 예외 처리)
try:
    from main import get_hankyung_consensus
except ImportError:
    # telegram 스텁 후 재시도 (python-telegram-bot 미설치 케이스)
    import importlib, types
    telegram_stub = types.ModuleType("telegram")
    telegram_stub.Update = object
    telegram_stub.ReplyKeyboardMarkup = type("ReplyKeyboardMarkup", (), {"__init__": lambda self, *a, **kw: None})
    ext_stub = types.ModuleType("telegram.ext")
    ext_stub.Application = object
    ext_stub.CommandHandler = object
    ext_stub.MessageHandler = object
    ext_stub.filters = type("filters", (), {"TEXT": None, "Regex": staticmethod(lambda x: x)})()
    ext_stub.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})()
    sys.modules.setdefault("telegram", telegram_stub)
    sys.modules.setdefault("telegram.ext", ext_stub)
    try:
        from main import get_hankyung_consensus
    except ImportError:
        pytest.skip(
            "get_hankyung_consensus 가 main 에서 제거됨 (컨센서스 소스 한경→FnGuide, "
            "fetch_fnguide_consensus 로 대체). 이 파일은 라이브 텔레그램 CI 스크립트이며 "
            "제거된 함수를 호출하므로 obsolete — 재작성/삭제 결정 대기.",
            allow_module_level=True,
        )


TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TICKER = "005930"


async def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"텔레그램 전송 실패 ({resp.status}): {body}", file=sys.stderr)


async def main():
    print(f"[CI] {TICKER} 컨센서스 조회 시작...")
    result = await get_hankyung_consensus(TICKER, debug=True)

    if result is None:
        result = {}

    consensus_target = result.get("consensus_target", 0) or 0
    reports = result.get("reports") or []
    debug_logs = result.get("debug") or []

    # 결과 메시지 구성
    lines = [
        f"[CI] 삼성전자({TICKER}) 컨센서스 테스트",
        f"목표가: {consensus_target:,.0f}원" if consensus_target else "목표가: 없음",
        f"리포트 수: {len(reports)}개",
        "",
        "── 디버그 로그 ──",
    ]
    lines += debug_logs if debug_logs else ["(없음)"]

    msg = "\n".join(lines)
    print(msg)
    await send_telegram(msg)

    # 성공 판정
    success = consensus_target > 0 or len(reports) >= 1
    if success:
        print("[CI] 테스트 통과")
        sys.exit(0)
    else:
        print("[CI] 테스트 실패 - 목표가·리포트 모두 없음", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
