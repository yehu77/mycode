from __future__ import annotations

import json
import sys


def _read_message() -> dict:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise EOFError
        stripped = line.decode("utf-8").strip()
        if not stripped:
            break
        key, value = stripped.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    length = int(headers["content-length"])
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


while True:
    try:
        message = _read_message()
    except EOFError:
        break

    method = message["method"]
    msg_id = message["id"]
    if method == "initialize":
        _write_message(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"version": "0.0.1"},
                    "capabilities": {"tools": {}, "resources": {}},
                },
            }
        )
        continue
    if method == "tools/list":
        _write_message(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo_text",
                            "description": "Return the provided text.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ]
                },
            }
        )
        continue
    if method == "resources/list":
        _write_message(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "resources": [
                        {
                            "uri": "docs://guide",
                            "name": "Guide",
                            "mimeType": "text/plain",
                            "description": "Example guide text",
                        }
                    ]
                },
            }
        )
        continue
    if method == "tools/call":
        params = message.get("params", {})
        text = params.get("arguments", {}).get("text", "")
        _write_message(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"echo:{text}"}],
                    "isError": False,
                },
            }
        )
        continue
    _write_message(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }
    )
