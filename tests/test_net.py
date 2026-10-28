import unittest
from unittest import mock

from iacore.http import FetchError

from iafetch import net
from iafetch.net import Client, Stopped, check_url
from tests.support import FakeArchive, http_error


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def client(fake, clock=None, **kw):
    clock = clock or Clock()
    return Client(1.0, 2.0, get=fake.get, opener=fake.open, clock=clock, sleep=clock.sleep, **kw), clock


class NetTest(unittest.TestCase):
    def test_every_request_says_who_we_are(self):  # R1, R2
        fake = FakeArchive()
        fake.add("x", [])
        c, _ = client(fake)
        c.get("https://archive.org/metadata/x")
        c.open("https://archive.org/download/x/x_meta.xml").read()
        self.assertEqual(len(fake.user_agents), 2)
        for ua in fake.user_agents:
            self.assertRegex(ua, r"^iafetch/\d+\.\d+\.\d+ \(\+https://github.com/wheidal/InternetArchiveDownloader; personal collection")

    def test_an_agent_run_names_the_agent(self):
        with mock.patch.dict("os.environ", {"IAFETCH_AGENT": "claude-opus-5-5"}):
            self.assertIn("; agent=claude-opus-5-5)", net.user_agent())
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertNotIn("agent=", net.user_agent())

    def test_requests_are_paced(self):  # §3.2
        fake = FakeArchive()
        fake.add("x", [])
        c, clock = client(fake)
        for _ in range(3):
            c.get("https://archive.org/metadata/x")
        self.assertEqual(clock.slept, [1.0, 1.0])
        c.open("https://archive.org/download/x/x_meta.xml")
        self.assertEqual(clock.slept[-1], 2.0)

    def test_a_429_backs_off_for_everyone_then_retries(self):  # R4
        fake = FakeArchive()
        fake.add("x", [])
        real, answers = fake.get, [429]

        def get(url, **kw):
            if answers:
                code = answers.pop(0)
                raise FetchError(f"{url}: HTTP {code}") from http_error(url, code, {"Retry-After": "40"})
            return real(url, **kw)
        clock = Clock()
        c = Client(1.0, 2.0, get=get, opener=fake.open, clock=clock, sleep=clock.sleep)
        c.get("https://archive.org/metadata/x")
        self.assertGreaterEqual(sum(clock.slept), 40)
        self.assertLessEqual(sum(clock.slept), 40 * 1.2 + 1)
        self.assertEqual(c.refusals, 0)

    def test_a_download_429_holds_back_the_next_request(self):
        fake = FakeArchive()
        fake.add("x", [])
        fake.fail["x_meta.xml"] = [429]
        c, clock = client(fake)
        with self.assertRaises(Exception):
            c.open("https://archive.org/download/x/x_meta.xml")
        c.get("https://archive.org/metadata/x")
        self.assertGreaterEqual(clock.now, 1001.0)  # Retry-After: 1

    def test_the_breaker_stops_the_run(self):  # R5
        fake = FakeArchive()
        fake.add("x", [])
        fake.fail["x_meta.xml"] = [403] * 10
        c, _ = client(fake)
        for _ in range(net.STOP_AFTER - 1):
            with self.assertRaises(Exception) as ctx:
                c.open("https://archive.org/download/x/x_meta.xml")
            self.assertNotIsInstance(ctx.exception, Stopped)
        with self.assertRaisesRegex(Stopped, "info@archive.org"):
            c.open("https://archive.org/download/x/x_meta.xml")

    def test_a_success_resets_the_breaker(self):
        fake = FakeArchive()
        fake.add("x", [])
        fake.fail["x_meta.xml"] = [403] * (net.STOP_AFTER - 1)
        c, _ = client(fake)
        for _ in range(net.STOP_AFTER - 1):
            with self.assertRaises(Exception):
                c.open("https://archive.org/download/x/x_meta.xml")
        c.open("https://archive.org/download/x/x_meta.xml")
        self.assertEqual(c.refusals, 0)

    def test_404_is_not_retried(self):  # R4b
        fake = FakeArchive()
        c, _ = client(fake)
        with self.assertRaisesRegex(FetchError, "404"):
            c.get("https://archive.org/nothing")
        self.assertEqual(len(fake.calls), 1)

    def test_only_archive_org_and_never_disallowed_paths(self):  # R13
        check_url("https://archive.org/metadata/x")
        check_url("https://ia800300.us.archive.org/1/items/x/y.mp4")
        for url in ("http://archive.org/metadata/x", "https://example.org/x", "https://archive.org.evil.com/x",
                    "https://archive.org/control/x", "https://archive.org/report/x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_url(url)


if __name__ == "__main__":
    unittest.main()
