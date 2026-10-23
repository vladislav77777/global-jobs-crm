"""Small CLI for the agent's collection lease and verified progress."""
import argparse
import json
import sys
from pathlib import Path
from .collector import RunState


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["begin", "renew", "progress", "status", "end"])
    p.add_argument("--db", type=Path, default=Path(".local/collector.db"))
    p.add_argument("--token-file", type=Path)
    p.add_argument("--source")
    p.add_argument("--cursor-file", type=Path)
    p.add_argument("--complete", action="store_true")
    p.add_argument("--verified", action="store_true")
    a = p.parse_args()
    state = RunState(a.db)
    if a.action == "begin":
        token = state.acquire()
        token_file = a.token_file or a.db.parent / f"run-{token}.token"
        try:
            token_file.parent.mkdir(parents=True, exist_ok=True)
            with token_file.open("x", encoding="utf-8") as handle:
                handle.write(token)
        except Exception:
            state.release(token)
            raise
        print(json.dumps({"token_file": str(token_file)}))
    elif a.action == "status":
        print(json.dumps({"checkpoints": dict(state.db.execute("SELECT source,cursor FROM checkpoint")), "progress": [{"source": s, "cursor": json.loads(c), "complete": bool(v)} for s,c,v in state.db.execute("SELECT source,cursor,complete FROM progress")]}, ensure_ascii=False))
    else:
        if not a.token_file:
            p.error("This action requires this run's unique --token-file")
        token = a.token_file.read_text(encoding="utf-8").strip()
        if a.action == "end":
            state.release(token)
            print("Collection lease released")
        elif a.action == "renew":
            state.checkpoint(token, "", None, complete=False, writes_verified=False)
            print("Collection lease renewed")
        else:
            if not a.source or not a.cursor_file:
                p.error("progress requires --source and --cursor-file")
            state.checkpoint(token, a.source, json.loads(a.cursor_file.read_text(encoding="utf-8-sig")), complete=a.complete, writes_verified=a.verified)
            print("Verified progress saved" if a.verified else "Cursor not advanced: writes unverified")
    state.db.close()


if __name__ == "__main__":
    main()
