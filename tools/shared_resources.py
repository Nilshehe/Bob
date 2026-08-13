import threading

class PriorityGPULock:
    """
    Interaktiva chattanrop (acquire_interactive) går alltid före
    code_ai:s bakgrundsjobb (acquire_background). Bakgrundsjobbet
    håller aldrig låset längre än ETT LLM-anrop i taget, så en
    väntande interaktiv fråga kan klämma sig in mellan jobbets steg
    istället för att vänta tills hela jobbet är klart.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._waiting_interactive = 0
        self._cv = threading.Condition()

    def acquire_interactive(self):
        with self._cv:
            self._waiting_interactive += 1
        self._lock.acquire()
        with self._cv:
            self._waiting_interactive -= 1

    def acquire_background(self):
        while True:
            with self._cv:
                while self._waiting_interactive > 0:
                    self._cv.wait(timeout=0.05)
            if self._lock.acquire(timeout=0.05):
                return

    def release(self):
        self._lock.release()

GPU_LOCK = PriorityGPULock()