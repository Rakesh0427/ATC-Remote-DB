"""Local page for converting an ATC database image to a .vhf uplink file.

Run: python gui.py
Then open the printed URL. Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from codec import CodecError, decode_vhf, encode_vhf, inspect_bin

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "web" / "index.html"
SAMPLE = ROOT / "samples" / "SGC10001.bin"


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    raw = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length > 8_000_000:
        raise CodecError("upload is too large")
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise CodecError("request was not JSON") from exc


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            data = INDEX.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/sample.bin":
            data = SAMPLE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = _read_json(self)
            if self.path == "/api/inspect":
                import base64

                data = base64.b64decode(body["data_b64"])
                info = inspect_bin(data)
                _json(self, 200, _info_dict(info))
                return
            if self.path == "/api/convert":
                import base64

                data = base64.b64decode(body["data_b64"])
                result = encode_vhf(
                    data,
                    label=str(body.get("label") or "C1"),
                    address=str(body.get("address") or "#ID#"),
                    ack=str(body.get("ack") or "ACK"),
                    delay_ms=int(body.get("delay_ms") or 1000),
                )
                stem = Path(str(body.get("filename") or "atc-db")).name
                if stem.lower().endswith(".bin"):
                    stem = stem[:-4]
                preview_lines = [ln for ln in result.text.splitlines() if ln.startswith("<SOH>")]
                if len(preview_lines) > 4:
                    shown = preview_lines[:2] + ["..."] + preview_lines[-1:]
                else:
                    shown = preview_lines
                preview = "\r\n".join(shown)
                _json(
                    self,
                    200,
                    {
                        "filename": f"{stem}.vhf",
                        "vhf": result.text,
                        "preview": preview,
                        "stats": {
                            "session": result.session,
                            "size": result.size,
                            "file_crc32": result.file_crc32,
                            "data_slices": result.data_slices,
                            "air_blocks": result.air_blocks,
                            "messages": result.messages,
                            "max_text": result.max_text,
                            "label": result.label,
                            "warnings": result.warnings,
                        },
                    },
                )
                return
            if self.path == "/api/decode":
                import base64

                image = decode_vhf(str(body.get("vhf") or ""))
                info = inspect_bin(image)
                _json(
                    self,
                    200,
                    {
                        "size": len(image),
                        "file_crc32": info.file_crc32,
                        "recognized": info.recognized,
                        "part": info.part,
                        "centres": len(info.centres),
                        "data_b64": base64.b64encode(image).decode("ascii"),
                    },
                )
                return
            self.send_error(404)
        except (CodecError, KeyError, ValueError) as exc:
            _json(self, 400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — surface a short message to the page
            _json(self, 500, {"error": str(exc)})


def _info_dict(info) -> dict:
    return {
        "size": info.size,
        "file_crc32": info.file_crc32,
        "recognized": info.recognized,
        "db_id": info.db_id,
        "part": info.part,
        "slots": info.slots,
        "empty_slots": info.empty_slots,
        "trailer_hex": info.trailer_hex,
        "centres": [
            {
                "index": c.index,
                "gfd": c.gfd,
                "name": c.name,
                "country": c.country,
                "tsel": c.tsel,
                "tsel_hex": c.tsel_hex,
            }
            for c in info.centres
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ATC database to VHF uplink tool")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ATC uplink builder at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
