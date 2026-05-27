"""main_pkg — main.py 패키지 분할.

기존 `python main.py` shim이 이 패키지의 main()을 호출한다.
"""
from main_pkg._entry import main, post_init, _run_all
