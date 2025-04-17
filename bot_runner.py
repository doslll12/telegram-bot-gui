import asyncio
from bot_gui import (
    ensure_config_files,
    load_accounts,
    login_accounts,
    account_task,
    run_copy_monitor,
    update_alert_handlers,
)

if __name__ == "__main__":
    # 1) 설정 파일 초기화
    ensure_config_files()

    # 2) 새 이벤트 루프 생성 & 기본 루프로 세팅
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 3) 계정 로그인 (세션 생성 및 이벤트 핸들러 등록)
    loop.run_until_complete(login_accounts())

    # 4) 각 계정별 백그라운드 태스크 시작
    for acc in load_accounts():
        loop.create_task(account_task(acc))

    # 5) 방배끼기 모니터 켜기
    run_copy_monitor()

    # 6) 알림 핸들러 등록 상태 갱신
    update_alert_handlers()

    # 7) 무한 대기 → Ctrl+C로 종료
    print("▶ Bot runner started. Press Ctrl+C to stop.")
    loop.run_forever()
