import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rate_limit import WINDOW_SECONDS, RateLimiter


class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def limiter(budget=100_000, in_flight=3, jitter=0.0):
    fake = FakeTime()
    return RateLimiter(budget, in_flight, clock=fake.clock, sleep=fake.sleep, jitter=lambda: jitter), fake


def test_requests_start_immediately_within_budget():
    lim, fake = limiter()
    tickets = [lim.acquire(), lim.acquire()]
    assert len(set(tickets)) == 2 and fake.sleeps == []


def test_waits_for_oldest_usage_to_leave_the_window():
    lim, fake = limiter(budget=100_000)
    lim.release(lim.acquire(), 90_000)
    fake.now = 10.0
    lim.acquire()
    assert fake.now >= WINDOW_SECONDS  # 90k from t=0 had to expire first


def test_cool_down_pauses_everyone_with_jitter():
    lim, fake = limiter(jitter=1.5)
    lim.cool_down(20)
    lim.acquire()
    assert fake.sleeps == [21.5]
    lim.cool_down(5)  # counted from now
    lim.acquire()
    assert fake.sleeps[-1] == 6.5


def test_in_flight_cap():
    lim, fake = limiter(in_flight=1)
    first = lim.acquire()
    done = []

    def second():
        lim.acquire()
        done.append(True)
    # release the first from the fake sleep so the waiting caller can proceed
    fake.sleep = lambda s: (lim.release(first, 1_000), fake.sleeps.append(s))
    lim._sleep = fake.sleep
    thread = threading.Thread(target=second)
    thread.start()
    thread.join(timeout=2)
    assert done == [True] and fake.sleeps


def test_estimate_follows_real_usage_and_oversized_requests_still_run():
    lim, fake = limiter(budget=50_000)
    lim.release(lim.acquire(), 10_000)
    assert lim.estimate < 40_000
    lim2, fake2 = limiter(budget=10_000)
    lim2.acquire()  # estimate 40k > budget: capped, runs when nothing else is in the window
    assert fake2.sleeps == []


def test_rejected_request_uses_no_budget():
    lim, fake = limiter(budget=50_000)
    lim.release(lim.acquire(), 0)
    lim.acquire()
    assert fake.sleeps == []
