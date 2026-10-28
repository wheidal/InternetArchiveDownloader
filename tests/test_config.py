import tempfile
import unittest
from pathlib import Path

from iafetch.config import ConfigError, acknowledge_terms, load_config, parse_config


class ConfigTest(unittest.TestCase):
    def test_defaults_live_under_the_nas_root(self):
        c = parse_config({})
        self.assertEqual(c.root, Path("/mnt/synology/InternetArchive"))
        self.assertEqual(c.inbox, c.root / "_Inbox")
        self.assertEqual(c.archive, c.root / "_Archive")
        self.assertEqual(c.folder("movies"), c.root / "Movies")
        self.assertEqual(c.folder("no-such-kind"), c.root / "Other")
        self.assertEqual(c.handlers["GratefulDead"], "deadtag")

    def test_paths_outside_the_root_are_refused(self):  # R21
        for raw in ({"inbox": "/mnt/synology/Music/Inbox"}, {"archive": "../Elsewhere"},
                    {"library": {"movies": "/mnt/synology/Movies"}}, {"library": {"tv": "../TV"}}):
            with self.subTest(raw=raw), self.assertRaises(ConfigError):
                parse_config(raw)

    def test_root_must_be_absolute_and_kinds_known(self):
        with self.assertRaises(ConfigError):
            parse_config({"root": "InternetArchive"})
        with self.assertRaises(ConfigError):
            parse_config({"library": {"films": "Films"}})

    def test_a_missing_root_stops_and_is_never_created(self):  # R22
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "InternetArchive"
            c = parse_config({"root": str(root)})
            with self.assertRaisesRegex(ConfigError, "create it by hand"):
                c.check_root()
            self.assertFalse(root.exists())
            root.mkdir()
            c.check_root()

    def test_inside_refuses_paths_that_escape_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = parse_config({"root": tmp})
            self.assertEqual(c.inside(Path(tmp) / "Movies" / "x"), (Path(tmp) / "Movies" / "x").resolve())
            with self.assertRaises(ConfigError):
                c.inside(Path(tmp) / ".." / "x")
            outside = Path(tmp) / "link"
            outside.symlink_to("/")
            with self.assertRaises(ConfigError):
                c.inside(outside / "etc")

    def test_politeness_can_be_stricter_never_looser(self):
        c = parse_config({"net": {"api_pause": 3, "download_pause": 5, "concurrency": 2, "rate": "1M"}})
        self.assertEqual((c.net.api_pause, c.net.download_pause, c.net.concurrency, c.net.rate), (3, 5, 2, 1024 ** 2))
        for net in ({"api_pause": 0.5}, {"download_pause": 0}, {"concurrency": 5}, {"concurrency": 0}, {"rate": "fast"}):
            with self.subTest(net=net), self.assertRaises(ConfigError):
                parse_config({"net": net})

    def test_acknowledging_the_terms_keeps_the_rest_of_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(f'root = "{tmp}"\n\n[net]\nrate = "2M"\n')
            c = load_config(path)
            acknowledge_terms(c, "2026-10-28")
            again = load_config(path)
            self.assertEqual(again.acknowledged_terms, "2026-10-28")
            self.assertEqual(again.net.rate, 2 * 1024 ** 2)
            self.assertEqual(again.root, Path(tmp))


if __name__ == "__main__":
    unittest.main()
