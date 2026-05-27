"""main.py — shim. 실제 로직은 main_pkg/ 패키지에 있음.
launchd plist가 `python main.py`를 실행하므로 이 파일은 그대로 유지.
"""
from main_pkg import main

if __name__ == "__main__":
    main()
