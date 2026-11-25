#!/usr/bin/env python3
"""GIT PERSISTENCE — fallback only.

  Primary ledger persistence is the Render persistent disk
  (LEDGER_PATH=/data/paper_trades.jsonl). This module runs only when
  the ledger is NOT on a disk (LEDGER_PATH unset) AND a GITHUB_PAT
  env var is set: it commits + pushes data/paper_trades.jsonl (and
  the scan log) back to the repo with the credential-helper form —
  credentials are NEVER embedded in a URL and NEVER logged.
  Without a PAT it logs a clear warning and the run stays stateless.
"""
import os
import subprocess
import time

_BASE = os.path.dirname(os.path.abspath(__file__))


def _run(args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(args, cwd=_BASE, capture_output=True, text=True,
                          env=e, timeout=120)


def persist_ledger():
    if os.environ.get("LEDGER_PATH"):
        print("[persist] ledger is on the persistent disk — git push-back skipped")
        return "disk"
    pat = os.environ.get("GITHUB_PAT", "")
    if not pat:
        print("[persist] WARNING: no persistent disk and no GITHUB_PAT — "
              "ledger will NOT survive restarts (stateless run)")
        return "stateless"
    try:
        if _run(["git", "rev-parse", "--git-dir"]).returncode != 0:
            print("[persist] not a git repo — skipping")
            return "no-repo"
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if not branch:
            branch = "main"
        _run(["git", "config", "user.email", "hedge-fund-bot@users.noreply.github.com"])
        _run(["git", "config", "user.name", "ai-hedge-fund-bot"])
        _run(["git", "add", "data/paper_trades.jsonl", "data/scan_log.jsonl",
              "data/gold_backtest.json"])
        status = _run(["git", "status", "--porcelain"]).stdout.strip()
        if not status:
            print("[persist] nothing new to push")
            return "clean"
        _run(["git", "commit", "-m",
              f"ledger update {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"])
        user = os.environ.get("GIT_USER", "ryhantrade")
        helper = ("!f() { echo username=%s; echo password=$GITHUB_PAT; }; f" % user)
        r = _run(["git", "-c", f"credential.helper={helper}",
                  "push", "origin", branch])
        if r.returncode == 0:
            print(f"[persist] ledger pushed to origin/{branch}")
            return "pushed"
        print(f"[persist] push failed: {r.stderr.strip()[:200]}")
        return "push-failed"
    except Exception as e:
        print(f"[persist] error: {str(e)[:200]}")
        return "error"


if __name__ == "__main__":
    print(persist_ledger())
