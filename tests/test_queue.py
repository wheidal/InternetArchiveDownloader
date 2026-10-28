import tempfile
import unittest
from pathlib import Path

from iafetch.queue import DONE, FAILED, PENDING, Entry, Queue


class QueueTest(unittest.TestCase):
    def test_add_list_retry_remove_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = Queue(Path(tmp) / "q.db")
            self.assertTrue(q.add(Entry("a", "A", 10, options={"formats": ["h.264"]})))
            self.assertFalse(q.add(Entry("a")))
            q.add(Entry("b"))
            q.add(Entry("c"))
            self.assertEqual([e.identifier for e in q.pending()], ["a", "b", "c"])
            self.assertEqual(q.get("a").options, {"formats": ["h.264"]})
            q.set("a", FAILED, "HTTP 404")
            q.set("b", DONE, result="/lib/b")
            self.assertEqual(q.get("a").error, "HTTP 404")
            self.assertEqual(q.retry(), 1)
            self.assertEqual([e.identifier for e in q.pending()], ["c", "a"])  # retried: at the end
            self.assertTrue(q.add(Entry("b")))  # done: may be queued again
            self.assertEqual(q.remove("c"), 1)
            self.assertEqual(q.clear(), 2)
            self.assertEqual(q.pending(), [])

    def test_two_queues_share_one_file(self):  # a queue add while get runs
        with tempfile.TemporaryDirectory() as tmp:
            q1, q2 = Queue(Path(tmp) / "q.db"), Queue(Path(tmp) / "q.db")
            q1.add(Entry("a"))
            q2.add(Entry("b"))
            q2.remove("a")
            self.assertEqual([e.identifier for e in q1.pending()], ["b"])
            self.assertFalse(q1.still_pending("a"))
            self.assertEqual(q1.get("b").status, PENDING)


if __name__ == "__main__":
    unittest.main()
