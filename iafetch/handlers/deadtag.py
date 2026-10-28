"""Grateful Dead items (§6.2) go to deadfetch, which ranks, downloads and files them with deadtag into its
own folders. iafetch downloads and writes nothing for them (R21)."""

import shutil
import subprocess

from .base import Handler


class HandOffError(Exception):
    pass


class Deadtag(Handler):
    name = "deadtag"
    downloads = False

    def hand_off(self, identifier: str, run=subprocess.run, which=shutil.which) -> str:
        """Queue the item with deadfetch; what it said."""
        exe = which("deadfetch")
        if not exe:
            raise HandOffError("deadfetch isn't installed (it's part of DeadShowTagger); Grateful Dead items "
                               "are downloaded with it")
        done = run([exe, "queue", "add", identifier], capture_output=True, text=True, timeout=300)
        said = (done.stdout + done.stderr).strip()
        if done.returncode:
            raise HandOffError(f"deadfetch queue add {identifier} failed: {said}")
        return said
