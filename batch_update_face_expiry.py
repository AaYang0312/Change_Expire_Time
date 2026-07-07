#!/usr/bin/env python3
import argparse
import csv
import json
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_URL = "http://192.168.100.5/face/http_req"
DEFAULT_OUTPUT_CSV = "faces_with_names.csv"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MODE = 2
REQUIRED_FACE_FIELDS = ("face_id", "img_data")
PERSON_NAME_FIELDS = ("per_name", "name", "person_name", "usr_name", "user_name")
CSV_FIELDS = ("query_id", "per_id", "per_name", "mode", "face_id", "s_time", "e_time")


def parse_int_or_text(value: str):
    value = str(value).strip()
    return int(value) if value.isdigit() else value


def parse_device_time(value: Optional[str], tz_name: str, *, end_of_day: bool = False):
    if not value:
        return None

    value = value.strip()
    if value.isdigit():
        return int(value)

    tz = ZoneInfo(tz_name)
    if len(value) == 10:
        parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
        parsed_time = dt_time(23, 59, 59) if end_of_day else dt_time(0, 0, 0)
        parsed = datetime.combine(parsed_date, parsed_time)
    else:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return int(parsed.timestamp())


def make_headers(url: str, cookie: str):
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "Accept": "text/plain, */*; q=0.01",
        "Accept-Encoding": "identity",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": origin,
        "Referer": f"{origin}/main.htm",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookie,
    }


def post_json(url: str, headers: dict, payload: dict, timeout: int = 10):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")

    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace").strip()
    except HTTPError as exc:
        status = exc.code
        text = exc.read().decode("utf-8", errors="replace").strip()
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc}") from exc

    try:
        data = json.loads(text) if text else {}
    except ValueError as exc:
        raise RuntimeError(f"response is not JSON: {text[:200]}") from exc

    return status, data, text


def iter_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def pick_person_name(record: dict, fallback_name: str = ""):
    for field in PERSON_NAME_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback_name.strip()


def find_face_record(response_data, per_id: Optional[str] = None):
    candidates = []
    for item in iter_dicts(response_data):
        if not all(field in item for field in REQUIRED_FACE_FIELDS):
            continue
        item_per_id = str(item.get("per_id") or item.get("id") or "")
        if per_id and item_per_id == str(per_id):
            return item
        candidates.append(item)

    if per_id:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return None


def find_person_name(response_data, per_id: str = ""):
    for item in iter_dicts(response_data):
        item_name = pick_person_name(item)
        if not item_name:
            continue
        item_per_id = str(item.get("per_id") or item.get("id") or "")
        if not per_id or item_per_id == str(per_id):
            return item_name
    return ""


def query_face(url: str, headers: dict, query_id, per_id: Optional[str] = None):
    payload = {"version": "0.2", "cmd": "query_face", "id": query_id}
    status, data, text = post_json(url, headers, payload)
    if status != 200:
        raise RuntimeError(f"query_face HTTP {status}: {text[:200]}")
    return find_face_record(data, per_id), data


def read_people(csv_path: Path, default_mode: int):
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        if not {"query_id", "record_id"} & fieldnames:
            raise SystemExit("CSV must contain query_id or record_id column")
        if "per_id" not in fieldnames:
            raise SystemExit("CSV must contain per_id column")
        if "per_name" not in fieldnames and "name" not in fieldnames:
            raise SystemExit("CSV must contain per_name or name column")

        for row in reader:
            query_id = (row.get("query_id") or row.get("record_id") or "").strip()
            per_id = (row.get("per_id") or "").strip()
            per_name = pick_person_name(row)
            if not query_id:
                continue
            if not per_id:
                raise RuntimeError(f"CSV row query_id={query_id} missing per_id")
            if not per_name:
                raise RuntimeError(f"CSV row query_id={query_id} per_id={per_id} missing per_name")
            mode = int((row.get("mode") or str(default_mode)).strip())
            yield {
                "query_id": parse_int_or_text(query_id),
                "per_id": per_id,
                "per_name": per_name,
                "mode": mode,
            }


def build_update_payload(record: dict, person: dict, s_time: Optional[int], e_time: int):
    missing = [field for field in REQUIRED_FACE_FIELDS if not record.get(field)]
    if missing:
        raise RuntimeError(f"query_face record missing required fields: {', '.join(missing)}")

    record_per_id = str(record.get("per_id") or "")
    if record_per_id and record_per_id != str(person["per_id"]):
        raise RuntimeError(f"query_face returned per_id={record_per_id}, expected {person['per_id']}")

    return {
        "version": "0.2",
        "cmd": "update_face_ex",
        "only_feature": int(record.get("only_feature", 1) or 1),
        "per_id": str(person["per_id"]),
        "face_id": str(record.get("face_id")),
        "per_name": str(person["per_name"]),
        "idcardNum": str(record.get("idcardNum") or ""),
        "img_data": str(record.get("img_data")),
        "idcardper": str(record.get("idcardper") or ""),
        "s_time": int(s_time if s_time is not None else record.get("s_time", 0)),
        "e_time": int(e_time),
        "per_type": int(record.get("per_type", 0) or 0),
        "usr_type": int(record.get("usr_type", 0) or 0),
    }


def build_timeleave_payload(per_id: str, mode: int):
    return {
        "version": "0.2",
        "cmd": "set_person_timeleave",
        "body": {"per_id": str(per_id), "mode": mode},
    }


def ensure_ack(label: str, status: int, data: dict, text: str):
    if status != 200:
        raise RuntimeError(f"{label} HTTP {status}: {text[:200]}")
    if data.get("code") not in (None, 0):
        raise RuntimeError(f"{label} returned code={data.get('code')}: {text[:200]}")


def scan_faces(args):
    headers = make_headers(args.url, args.cookie)
    rows = []
    consecutive_misses = 0
    found_count = 0

    for query_id in range(args.scan_from, args.scan_from + args.scan_limit):
        try:
            record, raw_data = query_face(args.url, headers, query_id)
        except Exception as exc:
            print(f"[FAIL] query_id={query_id} {exc}")
            break

        if not record:
            print(f"[MISS] query_id={query_id}")
            if found_count > 0:
                consecutive_misses += 1
                if consecutive_misses >= args.stop_after_misses:
                    print(f"[STOP] {consecutive_misses} consecutive missing records after last hit")
                    break
            time.sleep(args.delay)
            continue

        found_count += 1
        consecutive_misses = 0
        per_id = str(record.get("per_id") or "")
        if not per_id:
            print(f"[SKIP] query_id={query_id} record has no per_id")
            time.sleep(args.delay)
            continue

        per_name = pick_person_name(record) or find_person_name(raw_data, per_id)
        row = {
            "query_id": query_id,
            "per_id": per_id,
            "per_name": per_name,
            "mode": args.mode,
            "face_id": record.get("face_id", ""),
            "s_time": record.get("s_time", ""),
            "e_time": record.get("e_time", ""),
        }
        rows.append(row)
        print(f"[QUERY] query_id={query_id} per_id={per_id} name={per_name or '<missing>'}")
        time.sleep(args.delay)

    output_path = Path(args.output)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    missing_names = sum(1 for row in rows if not str(row.get("per_name", "")).strip())
    print(f"Exported: {output_path}")
    print(f"Found count: {found_count}")
    print(f"Exported rows: {len(rows)}")
    if missing_names:
        print(f"WARNING: {missing_names} exported records have empty per_name; fill them before update")


def update_from_csv(args):
    people = list(read_people(Path(args.csv), args.mode))
    if not people:
        raise RuntimeError("CSV has no usable rows")

    e_time = parse_device_time(args.e_time, args.timezone, end_of_day=True)
    s_time = parse_device_time(args.s_time, args.timezone)
    headers = make_headers(args.url, args.cookie)

    print(f"CSV rows: {len(people)}")
    print(f"Target e_time: {e_time}")
    if s_time is not None:
        print(f"Target s_time: {s_time}")

    if not args.yes:
        answer = input("确认开始写入设备？输入 yes 继续: ").strip().lower()
        if answer != "yes":
            print("Cancelled.")
            return

    ok_count = 0
    for index, person in enumerate(people, start=1):
        query_id = person["query_id"]
        per_id = person["per_id"]
        try:
            record, _ = query_face(args.url, headers, query_id, per_id)
            if not record:
                raise RuntimeError("query_face returned no usable face record")

            update_payload = build_update_payload(record, person, s_time, e_time)
            status, data, text = post_json(args.url, headers, update_payload)
            ensure_ack("update_face_ex", status, data, text)
            print(f"[OK] #{index} per_id={per_id} update_face_ex")

            status, data, text = post_json(args.url, headers, build_timeleave_payload(per_id, person["mode"]))
            ensure_ack("set_person_timeleave", status, data, text)
            print(f"[OK] #{index} per_id={per_id} set_person_timeleave mode={person['mode']}")
            ok_count += 1
            time.sleep(args.delay)
        except Exception as exc:
            print(f"[FAIL] #{index} query_id={query_id} per_id={per_id} {exc}")
            break

    print(f"Done. Updated count: {ok_count}")


def ask(prompt: str, default: Optional[str] = None, required: bool = True):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        if not required:
            return ""
        print("此项必填。")


def ask_int(prompt: str, default: int):
    while True:
        value = ask(prompt, str(default))
        try:
            return int(value)
        except ValueError:
            print("请输入数字。")


def ask_float(prompt: str, default: float):
    while True:
        value = ask(prompt, str(default))
        try:
            return float(value)
        except ValueError:
            print("请输入数字。")


def interactive_menu():
    print("智能门禁人脸过期时间工具")
    print("1. 扫描人员并导出 CSV")
    print("2. 按 CSV 批量修改过期时间")
    print("0. 退出")
    choice = ask("请选择模式", required=True)

    if choice == "1":
        args = argparse.Namespace(
            url=ask("接口 URL", DEFAULT_URL),
            cookie=ask("Cookie"),
            scan_from=ask_int("起始 query_id", 0),
            scan_limit=ask_int("扫描数量", 248),
            stop_after_misses=ask_int("命中后连续 miss 多少次停止", 10),
            output=ask("导出 CSV", DEFAULT_OUTPUT_CSV),
            mode=ask_int("set_person_timeleave mode", DEFAULT_MODE),
            delay=ask_float("请求间隔秒", 0.2),
        )
        scan_faces(args)
        return

    if choice == "2":
        args = argparse.Namespace(
            url=ask("接口 URL", DEFAULT_URL),
            cookie=ask("Cookie"),
            csv=ask("CSV 文件", DEFAULT_OUTPUT_CSV),
            e_time=ask("目标过期时间，例如 2037-12-31 23:59:59"),
            s_time=ask("开始时间，留空则保留原值", "", required=False) or None,
            timezone=ask("时区", DEFAULT_TIMEZONE),
            mode=DEFAULT_MODE,
            delay=ask_float("请求间隔秒", 0.2),
            yes=False,
        )
        update_from_csv(args)
        return

    if choice == "0":
        print("Bye.")
        return

    print("无效选择。")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Scan face records or update face expiry time from CSV."
    )
    subparsers = parser.add_subparsers(dest="command")

    scan = subparsers.add_parser("scan", help="scan query_face ids and export CSV")
    scan.add_argument("--url", default=DEFAULT_URL)
    scan.add_argument("--cookie", required=True)
    scan.add_argument("--scan-from", type=int, default=0)
    scan.add_argument("--scan-limit", type=int, default=248)
    scan.add_argument("--stop-after-misses", type=int, default=10)
    scan.add_argument("--output", default=DEFAULT_OUTPUT_CSV)
    scan.add_argument("--mode", type=int, default=DEFAULT_MODE)
    scan.add_argument("--delay", type=float, default=0.2)
    scan.set_defaults(func=scan_faces)

    update = subparsers.add_parser("update", help="update expiry time from CSV")
    update.add_argument("--url", default=DEFAULT_URL)
    update.add_argument("--cookie", required=True)
    update.add_argument("--csv", default=DEFAULT_OUTPUT_CSV)
    update.add_argument("--e-time", required=True)
    update.add_argument("--s-time")
    update.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    update.add_argument("--mode", type=int, default=DEFAULT_MODE)
    update.add_argument("--delay", type=float, default=0.2)
    update.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    update.set_defaults(func=update_from_csv)

    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        interactive_menu()
        return

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
