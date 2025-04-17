--- bot_runner.py
+++ bot_runner.py
@@
-if __name__ == "__main__":
-    # 1) 설정 파일 초기화
-    ensure_config_files()
-
-    # 2) 계정 로그인 (세션 저장 및 이벤트 핸들러 등록)
-    asyncio.run(login_accounts())
-
-    # 3) 각 계정별로 백그라운드 태스크 시작
-    for acc in load_accounts():
-        # account_task() 안에서 run_until_disconnected() 까지 처리해 줍니다.
-        asyncio.get_event_loop().create_task(account_task(acc))
-
-    # 4) 방배끼기 모니터 켜기
-    run_copy_monitor()
-
-    # 5) 알림 핸들러 등록 상태 갱신
-    update_alert_handlers()
-
-    # 6) 이벤트 루프를 계속 돌립니다
-    print("▶ Bot runner started. Press Ctrl+C to stop.")
-    asyncio.get_event_loop().run_forever()
+if __name__ == "__main__":
+    # 1) 설정 파일 초기화
+    ensure_config_files()
+
+    # 2) 새 이벤트 루프 만들고, 이걸 기본 루프로 설정
+    loop = asyncio.new_event_loop()
+    asyncio.set_event_loop(loop)
+
+    # 3) 계정 로그인 (세션 저장 및 이벤트 핸들러 등록)
+    loop.run_until_complete(login_accounts())
+
+    # 4) 각 계정별로 백그라운드 태스크 시작
+    for acc in load_accounts():
+        loop.create_task(account_task(acc))
+
+    # 5) 방배끼기 모니터 켜기
+    run_copy_monitor()
+
+    # 6) 알림 핸들러 등록 상태 갱신
+    update_alert_handlers()
+
+    # 7) 이제 루프를 꺼지지 않게 무한 대기
+    print("▶ Bot runner started. Press Ctrl+C to stop.")
+    loop.run_forever()
