"""Read-only client for HiringCafe's official Streamable HTTP MCP."""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://hiringcafe.com/api/mcp"


class HiringCafe:
    def __init__(self):
        self.session = None
        self.request_id = 0

    def request(self, method, params=None, notification=False):
        self.request_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self.request_id
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(ENDPOINT, json.dumps(payload).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read().decode()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HiringCafe HTTP {exc.code}; Retry-After={exc.headers.get('Retry-After', 'unspecified')}") from exc
        if not raw.strip():
            return None
        result = json.loads(raw)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result.get("result")

    def initialize(self):
        self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "global-jobs-crm", "version": "0.1"}})
        self.request("notifications/initialized", notification=True)

    def call(self, name, arguments):
        if name not in {"search_jobs", "get_job_details"}:
            raise ValueError("Only the two official read-only tools are allowed")
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(result)
        return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("method", choices=["tools", "search_jobs", "get_job_details"])
    parser.add_argument("--args", default="{}", help="JSON object; never include CV or personal contact data")
    parser.add_argument("--args-file", type=Path, help="UTF-8 JSON arguments, avoiding shell quoting")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    client = HiringCafe()
    client.initialize()
    arguments = json.loads(args.args_file.read_text(encoding="utf-8-sig") if args.args_file else args.args)
    result = client.request("tools/list") if args.method == "tools" else client.call(args.method, arguments)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({"saved": str(args.output), "bytes": len(text.encode())}))
    else:
        print(text)


if __name__ == "__main__":
    main()
