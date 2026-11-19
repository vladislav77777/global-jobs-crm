"""Move one owner's private continuation state between stopped local workspaces."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .collector import RunState
from .setup import check_config


MANIFEST = "transfer-manifest.json"
PRIVATE_FILES = {"applications.db", "collector.db", "CONTINUATION.md"}
EXCLUDED = {"__pycache__", ".git", ".venv", "transfers"}
SECRET_WORDS = {"cookie", "cookies", "credential", "credentials", "secret", "secrets", "session", "token"}


def allowed(name):
    path = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or path.is_absolute()
            or ".." in path.parts or str(path) != name):
        return False
    reserved = {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
    if any(part.endswith((".", " ")) or part.split(".")[0].upper() in reserved for part in path.parts):
        return False
    if any(part in EXCLUDED or part.startswith(".env") for part in path.parts):
        return False
    words = set(name.lower().replace(".", "-").replace("_", "-").replace("/", "-").split("-"))
    if words & SECRET_WORDS or name.endswith((".db-wal", ".db-shm", ".pid", ".zip")):
        return False
    if name in {"config.local.json", "AGENTS.md"}:
        return True
    if path.parts[0] == ".local" and len(path.parts) == 2:
        return path.name in PRIVATE_FILES or path.name.lower().startswith("cv-") or path.suffix.lower() == ".pdf"
    return path.parts[0] == "outputs" and len(path.parts) > 1


def read_config(root):
    config = json.loads((root / "config.local.json").read_text(encoding="utf-8-sig"))
    errors = check_config(config, require_notion=True)
    if errors:
        raise ValueError("Invalid private configuration: " + "; ".join(errors))
    if config["paths"] != {"private_dir": ".local", "output_dir": "outputs"}:
        raise ValueError("Transfer requires the standard .local/ and outputs/ directories")
    # This config contains account/Notion identifiers, never provider credentials.
    def check_keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                words = set(key.lower().replace("-", "_").split("_"))
                if not key.endswith("_env") and (words & (SECRET_WORDS | {"password"}) or key == "api_key"):
                    raise ValueError("Remove embedded credentials from config before transfer")
                check_keys(item)
        elif isinstance(value, list):
            for item in value:
                check_keys(item)
    check_keys(config)
    config["delivery"]["live_sending"] = False
    for key, value in config.get("cv", {}).items():
        if key.endswith("_path") and value:
            path = Path(value)
            if path.is_absolute():
                try:
                    config["cv"][key] = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    config["cv"][key] = ""  # Refresh an external CV from Notion on the new laptop.
    return config


def backup_database(source, target, clear_lease=False):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if clear_lease:
                dst.execute("PRAGMA secure_delete=ON")
                dst.execute("DELETE FROM lease")
                dst.commit()
                dst.execute("VACUUM")
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Invalid SQLite backup")


def export_workspace(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    # A private archive must stay in an ignored directory, never among tracked files.
    destination.relative_to(root / ".local" / "transfers")
    if destination.exists():
        raise FileExistsError("Choose a new archive name; an existing backup is never overwritten")
    config = read_config(root)
    for name in ("applications.db", "collector.db"):
        if not (root / ".local" / name).is_file():
            raise ValueError("Missing continuation database: " + name)
    state = RunState(root / ".local" / "collector.db")
    token = None
    try:
        token = state.acquire()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            backups = {}
            for name in ("applications.db", "collector.db"):
                backup_database(root / ".local" / name, scratch / name, name == "collector.db")
                backups[".local/" + name] = scratch / name
            files = {"config.local.json": json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")}
            candidates = [root / "AGENTS.md", *(root / ".local").iterdir(), *(root / "outputs").rglob("*")]
            for path in candidates:
                name = path.relative_to(root).as_posix()
                if path.is_symlink() or not path.is_file() or not allowed(name):
                    continue
                if not path.resolve().is_relative_to(root):
                    raise ValueError("Private path leaves the workspace")
                files[name] = backups.get(name, path)
            files.update(backups)
            manifest = {"format": "global-jobs-crm-continuation", "version": 1,
                        "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
            last_renewal = time.monotonic()
            try:
                with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                    for name, source in sorted(files.items()):
                        if time.monotonic() - last_renewal > 60:
                            state.checkpoint(token, "", None, complete=False, writes_verified=False)
                            last_renewal = time.monotonic()
                        data = source if isinstance(source, bytes) else source.read_bytes()
                        archive.writestr(name, data)
                        manifest["files"][name] = hashlib.sha256(data).hexdigest()
                    archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
                verify_archive(destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
        return {"archive": str(destination), "files": len(manifest["files"]),
                "bytes": destination.stat().st_size, "encrypted": False}
    finally:
        if token:
            state.release(token)
        state.db.close()


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len({name.casefold() for name in names}) != len(names) or names.count(MANIFEST) != 1:
            raise ValueError("Duplicate archive paths or missing manifest")
        manifest = json.loads(archive.read(MANIFEST))
        if manifest.get("format") != "global-jobs-crm-continuation" or manifest.get("version") != 1:
            raise ValueError("Unsupported continuation archive")
        files = manifest["files"]
        required = {"config.local.json", ".local/applications.db", ".local/collector.db"}
        if not required <= files.keys() or set(names) != set(files) | {MANIFEST}:
            raise ValueError("Incomplete archive inventory")
        for name, digest in files.items():
            info = archive.getinfo(name)
            if not allowed(name) or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe archive member")
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("Archive checksum mismatch")
        return manifest


def restore_workspace(root, source):
    root, source = Path(root).resolve(), Path(source).resolve()
    manifest = verify_archive(source)
    # Restore to a fresh checkout only. Never merge two independently changed ledgers.
    for name in ("config.local.json", ".local", "outputs", "AGENTS.md"):
        if (root / name).exists():
            raise FileExistsError("Restore into a fresh checkout; already exists: " + name)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        staged = Path(folder)
        with zipfile.ZipFile(source) as archive:
            for name in manifest["files"]:
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        config = read_config(staged)
        (staged / "config.local.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in ("applications.db", "collector.db"):
            with closing(sqlite3.connect((staged / ".local" / name).as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Invalid restored database")
                if name == "collector.db" and db.execute("SELECT count(*) FROM lease").fetchone()[0]:
                    raise ValueError("An active lease must never be restored")
        exclude = root / ".git" / "info" / "exclude"
        if (root / ".git").is_dir():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if "/AGENTS.md" not in existing.splitlines():
                with exclude.open("a", encoding="utf-8") as handle:
                    handle.write("\n/AGENTS.md\n")
        for name in (".local", "outputs", "AGENTS.md", "config.local.json"):
            source_path, target = staged / name, root / name
            if source_path.is_dir():
                # Synced Windows directories can be readable but locked against rename.
                shutil.copytree(source_path, target)
            elif source_path.is_file():
                with source_path.open("rb") as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
    return {"restored_files": len(manifest["files"]), "live_sending": False,
            "external_connections_checked": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "verify", "restore"])
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.action == "export":
        result = export_workspace(Path.cwd(), args.archive)
    elif args.action == "restore":
        result = restore_workspace(Path.cwd(), args.archive)
    else:
        result = {"valid": True, "files": len(verify_archive(args.archive)["files"])}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
