"""자동작업 동시 실행 제한 — '순번표'.

왜 필요한가 (2026-09-23 사고):
  12시 주문마감 때문에 모든 사용자의 Task 1·5가 12시 정각에 **동시에** 뜬다.
  9/23에는 5개가 겹치며 1.1GB를 먹었고, 2GB 서버의 메모리가 바닥나
  리눅스가 Streamlit 앱을 강제 종료(OOM)했다. 12:45 앱 사망, 이후 스왑
  1.5GB로 서버가 먹통. 사용자 장보기 목록은 12:56에야 나갔다(56분 지각).

무엇을 하나:
  실행 슬롯을 N개(기본 3)만 두고, 슬롯이 빌 때까지 기다렸다가 시작한다.
  크론은 그대로 12시 정각에 전부 뜨지만 **실제 동시 실행은 N개**가 된다.
  12시를 조금 넘겨 끝나더라도 앱이 죽지 않는 쪽이 낫다는 판단(사용자 확인).

왜 auto_task.py 맨 위에서 부르나:
  기다리는 동안 pandas·naver_api를 올려두면 대기 프로세스 20개가
  1.4GB를 잡아먹어 애초에 막으려던 사고가 그대로 난다. 이 모듈은
  표준 라이브러리만 쓰고, 순번을 받은 뒤에야 무거운 import가 시작된다.

슬롯 수 바꾸기: 환경변수 COSTCO_TASK_SLOTS, 또는 data/task_slots.txt 에 숫자.
"""
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOCK_DIR = os.path.join(DATA_DIR, "task_slots")
QUEUE_LOG = os.path.join(DATA_DIR, "task_queue.log")

DEFAULT_SLOTS = 3
POLL_SEC = 3
# 기본 대기 상한 40분. 넘으면 **그냥 실행한다** — 주문수집은 건너뛰는 것보다
# 늦더라도 도는 게 낫다. (슬롯을 잡은 프로세스가 죽으면 flock은 자동 해제되므로
# '죽은 잠금'이 남아 영원히 막히는 일은 없다.)
DEFAULT_MAX_WAIT = 2400

_HELD = []          # 열어둔 잠금 파일 — 프로세스가 살아 있는 동안 유지해야 한다


def _slots():
    v = os.environ.get("COSTCO_TASK_SLOTS", "").strip()
    if not v:
        try:
            with open(os.path.join(DATA_DIR, "task_slots.txt"), encoding="utf-8") as f:
                v = f.read().strip()
        except Exception:
            v = ""
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return DEFAULT_SLOTS


def _qlog(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(QUEUE_LOG) and os.path.getsize(QUEUE_LOG) > 1024 * 1024:
            with open(QUEUE_LOG, encoding="utf-8", errors="replace") as f:
                keep = f.readlines()[-2000:]
            with open(QUEUE_LOG, "w", encoding="utf-8") as f:
                f.writelines(keep)
        with open(QUEUE_LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _try_take(path):
    """슬롯 하나를 잡아 본다. 성공하면 열린 파일(잠금 유지), 실패하면 None."""
    try:
        import fcntl
    except ImportError:        # 윈도우(로컬 개발) — 제한 없이 그대로 실행
        return None
    try:
        fh = open(path, "a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fh.seek(0)
            fh.truncate()
            fh.write("pid=%d %s\n" % (os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S")))
            fh.flush()
        except Exception:
            pass
        return fh
    except Exception:
        try:
            fh.close()
        except Exception:
            pass
        return None


def acquire(label="task", slots=None, max_wait=None):
    """순번을 기다렸다가 슬롯을 잡는다. 잡으면 True, 못 잡고 그냥 진행하면 False.

    반환값은 기록용일 뿐 — 어느 쪽이든 호출자는 작업을 계속한다.
    """
    if os.environ.get("COSTCO_TASK_NOGATE", "").strip() == "1":
        return False                          # 화면에서 사람이 직접 누른 실행
    if os.name == "nt":                       # 로컬 윈도우에서는 제한하지 않는다
        return False
    try:
        import fcntl                          # noqa: F401  (없으면 제한 불가)
    except ImportError:
        return False

    n = int(slots or _slots())
    limit = int(max_wait if max_wait is not None else DEFAULT_MAX_WAIT)
    try:
        os.makedirs(LOCK_DIR, exist_ok=True)
    except Exception:
        return False

    started = time.time()
    waited_logged = False
    while True:
        for i in range(n):
            fh = _try_take(os.path.join(LOCK_DIR, "slot%d.lock" % i))
            if fh:
                _HELD.append(fh)
                waited = int(time.time() - started)
                if waited >= POLL_SEC:
                    _qlog("▶ %s 시작 (슬롯 %d/%d · %d초 대기)" % (label, i + 1, n, waited))
                return True
        if time.time() - started >= limit:
            _qlog("⚠ %s 대기 %d분 초과 — 슬롯 없이 그대로 실행합니다." % (label, limit // 60))
            return False
        if not waited_logged:
            _qlog("⏳ %s 대기 중 (동시 실행 %d개가 차 있음)" % (label, n))
            waited_logged = True
        time.sleep(POLL_SEC)


def gate_from_argv(argv=None):
    """auto_task.py 실행 인자에서 --task/--user를 훑어 순번을 받는다.
    argparse 전에 부르므로 여기서는 인자를 '엿보기'만 한다."""
    argv = list(argv if argv is not None else sys.argv[1:])
    task, user = "all", "admin"
    for i, a in enumerate(argv):
        if a == "--task" and i + 1 < len(argv):
            task = argv[i + 1]
        elif a.startswith("--task="):
            task = a.split("=", 1)[1]
        elif a == "--user" and i + 1 < len(argv):
            user = argv[i + 1]
        elif a.startswith("--user="):
            user = a.split("=", 1)[1]
    return acquire("%s/%s" % (task, user))
