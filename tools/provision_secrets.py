#!/usr/bin/env python3
"""Write the helmet's secrets into its "jsecrets" NVS partition.

Builds an NVS image (namespace "secrets", partition size 0x4000) holding any of:
  - cf_id / cf_secret: Cloudflare Access service token. The firmware
    (boards/jarvis-helmet/cf_access.cc) sends the CF-Access-Client-Id/-Secret headers to
    the OTA and WebSocket server over https/wss only.
  - mcp_token: access token of the local MCP WebSocket (boards/jarvis-helmet/
    local_mcp_server.cc, contract D27). When set, ws://<helmet-ip>:8080/ws only accepts
    clients that send ?token=<value> (tools/servo_calibrate.py --token or env
    JARVIS_MCP_TOKEN); without it anyone on the same network can move the mask.
Unless --dry-run is given, the image is written with esptool at the jsecrets offset 0x1C000
(boards/jarvis-helmet/partitions_jarvis_16m.csv); the firmware reads it at boot.

The image REPLACES the whole partition: a key that is not in it is removed from the helmet.
Values come from the options or from the environment variables CF_ACCESS_CLIENT_ID,
CF_ACCESS_CLIENT_SECRET and JARVIS_MCP_TOKEN (preferred: they stay out of the shell
history, and keeping them set writes every secret again on the next run). Secret values are
never printed, except the token made by --generate-mcp-token, shown once. The generated
.bin contains the secrets in clear text: it is written to a temporary directory and deleted
after flashing unless --out/--keep is used.

Needs the NVS generator (`pip install esp-idf-nvs-partition-gen`, or an ESP-IDF shell with
IDF_PATH set) and esptool for flashing (`pip install esptool`, included in ESP-IDF).

Examples:
  set CF_ACCESS_CLIENT_ID=... & set CF_ACCESS_CLIENT_SECRET=...      (Windows cmd)
  python tools/provision_secrets.py --port COM5
  python tools/provision_secrets.py --generate-mcp-token --port COM5  (+ CF token if set in env)
  set JARVIS_MCP_TOKEN=... & python tools/provision_secrets.py --port COM5
  python tools/provision_secrets.py --dry-run --out %TEMP%\\jsecrets-nvs.bin
  python tools/provision_secrets.py --clear --port COM5               (remove every secret)
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

PARTITION_OFFSET = 0x1C000
PARTITION_SIZE = 0x4000
NAMESPACE = "secrets"
ID_KEY = "cf_id"
SECRET_KEY = "cf_secret"
MCP_TOKEN_KEY = "mcp_token"
MAX_LENGTH = 128  # firmware limit for cf_id / cf_secret (cf_access.cc)
TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# mcp_token: URL "unreserved" characters only, so it never needs escaping in ?token=.
# The firmware accepts 1..64 (local_mcp_policy.h); fewer than 16 is refused here as weak.
MCP_TOKEN_RE = re.compile(r"[A-Za-z0-9._~-]+")
MCP_TOKEN_MIN = 16
MCP_TOKEN_MAX = 64
ENV_ID = "CF_ACCESS_CLIENT_ID"
ENV_SECRET = "CF_ACCESS_CLIENT_SECRET"
ENV_MCP_TOKEN = "JARVIS_MCP_TOKEN"


def fail(message: str, code: int = 1) -> None:
    print(f"Lỗi: {message}", file=sys.stderr)
    raise SystemExit(code)


def check_value(name: str, value: str) -> str:
    value = value.strip()
    if not value:
        fail(f"thiếu {name} (dùng --{name} hoặc biến môi trường)")
    if len(value) > MAX_LENGTH:
        fail(f"{name} dài {len(value)} ký tự, tối đa {MAX_LENGTH}")
    if not TOKEN_RE.match(value):
        fail(f"{name} chứa ký tự không hợp lệ (chỉ cho phép chữ, số, '.', '_', '-')")
    return value


def check_mcp_token(value: str) -> str:
    value = value.strip()
    if len(value) < MCP_TOKEN_MIN:
        fail(f"mcp-token quá ngắn ({len(value)} ký tự, tối thiểu {MCP_TOKEN_MIN}); "
             "dùng --generate-mcp-token để tạo token ngẫu nhiên")
    if len(value) > MCP_TOKEN_MAX:
        fail(f"mcp-token dài {len(value)} ký tự, tối đa {MCP_TOKEN_MAX}")
    if not MCP_TOKEN_RE.fullmatch(value):
        fail("mcp-token chứa ký tự không hợp lệ (chỉ cho phép chữ, số, '.', '_', '~', '-')")
    return value


def generate_mcp_token() -> str:
    """Random URL-safe token: 32 bytes from the OS CSPRNG, 43 characters."""
    return secrets.token_urlsafe(32)


def generator_command(csv_path: Path, bin_path: Path, size: int) -> list[str]:
    args = ["generate", str(csv_path), str(bin_path), hex(size)]
    if importlib.util.find_spec("esp_idf_nvs_partition_gen") is not None:
        return [sys.executable, "-m", "esp_idf_nvs_partition_gen", *args]
    idf_path = os.environ.get("IDF_PATH")
    if idf_path:
        script = Path(idf_path) / "components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py"
        if script.is_file():
            return [sys.executable, str(script), *args]
    fail("không tìm thấy nvs_partition_gen: chạy `pip install esp-idf-nvs-partition-gen` "
         "hoặc mở ESP-IDF shell (IDF_PATH)")
    raise AssertionError  # unreachable


def esptool_command(args: argparse.Namespace, bin_path: Path) -> list[str]:
    if importlib.util.find_spec("esptool") is None:
        fail("không tìm thấy esptool: chạy `pip install esptool` hoặc dùng ESP-IDF shell")
    try:
        major = int(importlib.metadata.version("esptool").split(".")[0])
    except (importlib.metadata.PackageNotFoundError, ValueError):
        major = 4
    write = "write-flash" if major >= 5 else "write_flash"
    return [sys.executable, "-m", "esptool", "--chip", args.chip, "--port", args.port,
            "--baud", str(args.baud), write, hex(args.offset), str(bin_path)]


def write_csv(path: Path, entries: list[tuple[str, str]]) -> None:
    lines = ["key,type,encoding,value", f"{NAMESPACE},namespace,,"]
    lines.extend(f"{key},data,string,{value}" for key, value in entries)
    # Owner-only permissions where the OS supports it: the CSV holds the secrets.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")


def run(command: list[str], what: str, hide: list[str]) -> None:
    # The command line itself never contains secret values (they are in the CSV); `hide`
    # masks them in case a tool echoes them in an error message.
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        for value in hide:
            output = output.replace(value, "***")
        fail(f"{what} thất bại (mã {result.returncode}):\n{output}")


def show_generated_token(token: str) -> None:
    print("=" * 72)
    print("TOKEN MCP MỚI - chỉ hiện MỘT lần, lưu ngay vào trình quản lý mật khẩu:")
    print()
    print(f"    {token}")
    print()
    print("Token chỉ có hiệu lực sau khi ảnh NVS được nạp thành công vào mũ.")
    print(f"Dùng với: đặt biến môi trường {ENV_MCP_TOKEN}=<token> rồi chạy")
    print("  python tools/servo_calibrate.py --host <IP-mũ>")
    print("Không dán token vào chat, commit, ảnh chụp màn hình. Mất token thì tạo token mới")
    print("bằng --generate-mcp-token và nạp lại (token cũ hết hiệu lực).")
    print("=" * 72, flush=True)


def collect_entries(args: argparse.Namespace) -> tuple[list[tuple[str, str]], str | None]:
    """Returns the (key, value) pairs for the image and the generated token, if any."""
    if args.clear:
        if args.id or args.secret or args.mcp_token or args.generate_mcp_token:
            fail("--clear không dùng cùng --id/--secret/--mcp-token/--generate-mcp-token")
        return [], None

    entries: list[tuple[str, str]] = []
    raw_id = (args.id or os.environ.get(ENV_ID, "")).strip()
    raw_secret = (args.secret or os.environ.get(ENV_SECRET, "")).strip()
    if raw_id or raw_secret:
        client_id = check_value("id", raw_id)
        secret = check_value("secret", raw_secret)
        if not client_id.endswith(".access"):
            print("Cảnh báo: Client ID thường có dạng <32 hex>.access (gửi đủ cả '.access').")
        if args.secret:
            print("Lưu ý: --secret nằm trong lịch sử shell; nên dùng biến môi trường.")
        entries += [(ID_KEY, client_id), (SECRET_KEY, secret)]

    generated = None
    if args.generate_mcp_token:
        if os.environ.get(ENV_MCP_TOKEN, "").strip():
            print(f"Lưu ý: bỏ qua {ENV_MCP_TOKEN} đang đặt; sau khi nạp, đổi biến này thành "
                  "token mới.")
        generated = generate_mcp_token()
        token = generated
    elif args.mcp_token is not None:
        # An explicit empty value (e.g. an unset shell variable) is a mistake, not "no token".
        if not args.mcp_token.strip():
            fail(f"--mcp-token rỗng: muốn mũ không cần token thì bỏ hẳn tùy chọn này (và biến "
                 f"môi trường {ENV_MCP_TOKEN}); muốn token mới thì dùng --generate-mcp-token")
        token = check_mcp_token(args.mcp_token)
        print(f"Lưu ý: --mcp-token nằm trong lịch sử shell; nên dùng biến môi trường "
              f"{ENV_MCP_TOKEN}.")
    else:
        token = os.environ.get(ENV_MCP_TOKEN, "").strip()
        if token:
            token = check_mcp_token(token)
    if token:
        entries.append((MCP_TOKEN_KEY, token))

    if not entries:
        fail(f"không có gì để ghi: cần token Cloudflare (--id/--secret hoặc {ENV_ID}/"
             f"{ENV_SECRET}), token MCP (--mcp-token, {ENV_MCP_TOKEN} hoặc "
             "--generate-mcp-token), hoặc --clear để xóa hết")
    return entries, generated


def warn_missing(entries: list[tuple[str, str]], cleared: bool) -> None:
    if cleared:
        print("Lưu ý: khi nạp, mọi bí mật trên mũ bị xóa: không còn token Cloudflare, và server MCP "
              "cục bộ (cổng 8080) không cần token nữa (ai cùng mạng cũng điều khiển được mặt nạ).")
        return
    keys = {key for key, _ in entries}
    if ID_KEY not in keys:
        print(f"Lưu ý: ảnh không có {ID_KEY}/{SECRET_KEY}: khi nạp, token Cloudflare đang lưu trên "
              f"mũ (nếu có) bị xóa. Muốn giữ thì đặt {ENV_ID}/{ENV_SECRET} (hoặc --id/--secret).")
    if MCP_TOKEN_KEY not in keys:
        print(f"Lưu ý: ảnh không có {MCP_TOKEN_KEY}: khi nạp, server MCP cục bộ của mũ (cổng 8080) "
              "không còn cần token, ai cùng mạng cũng điều khiển được mặt nạ. Muốn giữ thì đặt "
              f"{ENV_MCP_TOKEN} (hoặc --mcp-token / --generate-mcp-token).")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Tạo ảnh NVS 'jsecrets' (token Cloudflare Access, token MCP cục bộ) và nạp "
                    "vào mũ. Ảnh thay toàn bộ phân vùng: khóa nào không có trong ảnh sẽ bị xóa.",
    )
    parser.add_argument("--id", help=f"Client ID (<32 hex>.access); mặc định env {ENV_ID}")
    parser.add_argument("--secret", help=f"Client Secret; mặc định env {ENV_SECRET}")
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument("--mcp-token",
                             help=f"token cho ws://<IP-mũ>:8080/ws ({MCP_TOKEN_MIN}-{MCP_TOKEN_MAX} "
                                  f"ký tự A-Z a-z 0-9 . _ ~ -); mặc định env {ENV_MCP_TOKEN}")
    token_group.add_argument("--generate-mcp-token", action="store_true",
                             help="tạo token MCP ngẫu nhiên (43 ký tự) và in ra MỘT lần")
    parser.add_argument("--clear", action="store_true",
                        help="tạo ảnh rỗng (xóa mọi bí mật khỏi mũ)")
    parser.add_argument("--port", help="cổng serial, ví dụ COM5 hoặc /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument("--chip", default="esp32s3")
    parser.add_argument("--offset", type=lambda s: int(s, 0), default=PARTITION_OFFSET,
                        help="offset phân vùng jsecrets (mặc định 0x1C000)")
    parser.add_argument("--size", type=lambda s: int(s, 0), default=PARTITION_SIZE,
                        help="kích thước phân vùng (mặc định 0x4000)")
    parser.add_argument("--out", type=Path, help="đường dẫn file .bin (mặc định: thư mục tạm)")
    parser.add_argument("--keep", action="store_true", help="giữ file .bin sau khi nạp")
    parser.add_argument("--dry-run", action="store_true", help="chỉ tạo file .bin, không nạp")
    args = parser.parse_args(argv)

    if args.size % 4096 or args.size < 0x3000:
        fail("--size phải là bội số của 4096 và >= 0x3000")
    if not args.dry_run and not args.port:
        fail("cần --port để nạp (hoặc --dry-run để chỉ tạo file)")

    entries, generated = collect_entries(args)

    with tempfile.TemporaryDirectory(prefix="jarvis-secrets-") as tmp:
        tmp_dir = Path(tmp)
        csv_path = tmp_dir / "jsecrets.csv"
        if args.out:
            bin_path = args.out.resolve()
        elif args.dry_run or args.keep:
            bin_path = Path(tempfile.gettempdir()) / "jarvis-jsecrets-nvs.bin"
        else:
            bin_path = tmp_dir / "jsecrets-nvs.bin"
        bin_path.parent.mkdir(parents=True, exist_ok=True)

        hide = [value for _, value in entries]
        write_csv(csv_path, entries)
        run(generator_command(csv_path, bin_path, args.size), "Tạo ảnh NVS", hide)
        csv_path.unlink(missing_ok=True)

        if not entries:
            print(f"Đã tạo ảnh NVS rỗng ({bin_path.stat().st_size} byte).", flush=True)
        else:
            keys = ", ".join(f"{key} ({len(value)} ký tự)" for key, value in entries)
            print(f"Đã tạo ảnh NVS: namespace '{NAMESPACE}', {keys}, "
                  f"{bin_path.stat().st_size} byte.", flush=True)
        warn_missing(entries, args.clear)
        if generated:
            show_generated_token(generated)

        if args.dry_run:
            print(f"File: {bin_path}")
            if entries:
                print("File chứa bí mật ở dạng rõ: xóa sau khi dùng, không commit.")
            return 0

        print(f"Nạp vào {args.port} tại {hex(args.offset)} ...", flush=True)
        run(esptool_command(args, bin_path), "Nạp bằng esptool", hide)
        print("Xong. Mũ tự khởi động lại (nếu không, bấm RST). Log xác nhận:")
        keys = {key for key, _ in entries}
        if ID_KEY in keys:
            print("  CfAccess: Service token loaded from NVS (...)")
        if MCP_TOKEN_KEY in keys:
            print("  LocalMcp: MCP WebSocket server on ws://<ip>:8080/ws (access token required)")
        else:
            print("  LocalMcp: MCP WebSocket server on ws://<ip>:8080/ws WITHOUT authentication ...")
        if args.out or args.keep:
            print(f"File giữ lại: {bin_path} (chứa bí mật, xóa sau khi dùng).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
