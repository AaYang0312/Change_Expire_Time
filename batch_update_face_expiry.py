#!/usr/bin/env python3
import argparse
import csv
import json
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


DEFAULT_URL = "http://192.168.100.5/face/http_req"
REQUIRED_FACE_FIELDS = ("face_id", "img_data")
PERSON_NAME_FIELDS = ("per_name", "name", "person_name", "usr_name", "user_name")


def parse_int_or_text(value: str):
    value = str(value).strip()
    return int(value) if value.isdigit() else value


# def read_people(csv_path: Path, default_mode: int):
#     with csv_path.open(newline="", encoding="utf-8-sig") as f:
#         reader = csv.DictReader(f)
#         fieldnames = set(reader.fieldnames or [])
#         if not ({"per_id", "query_id", "record_id"} & fieldnames):
#             raise SystemExit("CSV must contain per_id, query_id, or record_id column")
#         for row in reader:
#             per_id = (row.get("per_id") or "").strip()
#             query_id = (row.get("query_id") or row.get("record_id") or per_id).strip()
#             if not query_id:
#                 continue
#             mode = int((row.get("mode") or str(default_mode)).strip())
#             per_name = pick_person_name(row)
#             yield {"per_id": per_id, "query_id": parse_int_or_text(query_id), "mode": mode, "per_name": per_name}

def read_people(csv_path: Path, default_mode: int):
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        if not ({"per_id", "query_id", "record_id"} & fieldnames):
            raise SystemExit("CSV must contain per_id, query_id, or record_id column")
        for row in reader:
            per_id = (row.get("per_id") or "").strip()
            query_id = (row.get("query_id") or row.get("record_id") or "").strip()
            if not query_id:
                continue
            mode = int((row.get("mode") or str(default_mode)).strip())
            per_name = pick_person_name(row)
            yield {"per_id":per_id, "query_id":parse_int_or_text(query_id), "mode":mode, "per_name":per_name}




def parse_record_ids(value: str):
    record_ids = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            record_ids.append(parse_int_or_text(part))
            continue
        start_text, end_text = [piece.strip() for piece in part.split("-", 1)]
        start = int(start_text)
        end = int(end_text)
        step = 1 if end >= start else -1
        record_ids.extend(range(start, end + step, step))
    return record_ids


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
        "Referer": f"{origin}/main.htm?version=1783385867461",
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


def query_face(url: str, headers: dict, query_id, per_id: Optional[str] = None):
    payload = {"version": "0.2", "cmd": "query_face", "id": query_id}
    status, data, text = post_json(url, headers, payload)
    if status != 200:
        raise RuntimeError(f"query_face HTTP {status}: {text[:200]}")
    return find_face_record(data, per_id), data


def pick_person_name(record: dict, fallback_name: str = ""):
    for field in PERSON_NAME_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback_name.strip()


def find_person_name(response_data, per_id: str = ""):
    for item in iter_dicts(response_data):
        item_name = pick_person_name(item)
        if not item_name:
            continue
        item_per_id = str(item.get("per_id") or item.get("id") or "")
        if not per_id or item_per_id == str(per_id):
            return item_name
    return ""


def build_update_payload(record: dict, per_id: str, s_time: Optional[int], e_time: int, per_name: str = ""):
    missing = [field for field in REQUIRED_FACE_FIELDS if not record.get(field)]
    if missing:
        raise RuntimeError(f"query_face record missing required fields: {', '.join(missing)}")
    selected_name = per_name.strip() or pick_person_name(record)
    if not selected_name:
        raise RuntimeError("query_face record missing per_name; provide --per-name for one test or a per_name column in CSV")

    return {
        "version": "0.2",
        "cmd": "update_face_ex",
        "only_feature": int(record.get("only_feature", 1) or 1),
        "per_id": str(record.get("per_id") or record.get("id") or per_id),
        "face_id": str(record.get("face_id")),
        "per_name": selected_name,
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


def main():
    parser = argparse.ArgumentParser(
        description="Batch update face expiry time using query_face -> update_face_ex -> set_person_timeleave."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path, help="CSV with per_id column; use the person number shown on the web page")
    source.add_argument("--per-id-range", help="Person per_id ids/ranges, for example '0-427' or '133,425'")
    source.add_argument("--record-ids", help="Fallback: query_face record ids/ranges, for example '228-247' or '247-228,300'")
    source.add_argument("--scan-from", type=int, help="Fallback: scan query_face ids upward from this id")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--cookie", required=True, help="Value after 'Cookie:' from DevTools")
    parser.add_argument("--s-time", help="Start time. Epoch seconds or 'YYYY-MM-DD HH:MM:SS'. Default: keep existing")
    parser.add_argument("--e-time", required=True, help="End time. Epoch seconds or 'YYYY-MM-DD HH:MM:SS'")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--mode", type=int, default=2, help="Default set_person_timeleave mode")
    parser.add_argument("--per-name", help="Fallback per_name when query_face does not return it; use only for one-person tests")
    parser.add_argument("--scan-limit", type=int, default=10000, help="Safety cap for --scan-from")
    parser.add_argument(
        "--stop-after-misses",
        type=int,
        default=10,
        help="In --scan-from mode, stop after this many consecutive missing records after the first hit",
    )
    parser.add_argument("--export-csv", type=Path, help="Export checked records without image data")
    parser.add_argument("--check-records", action="store_true", help="Only query records and print sanitized field checks")
    parser.add_argument("--apply", action="store_true", help="Actually send update_face_ex and set_person_timeleave")
    args = parser.parse_args()

    if args.scan_limit < 1:
        raise SystemExit("--scan-limit must be >= 1")
    if args.stop_after_misses < 1:
        raise SystemExit("--stop-after-misses must be >= 1")

    s_time = parse_device_time(args.s_time, args.timezone)
    e_time = parse_device_time(args.e_time, args.timezone, end_of_day=True)
    headers = make_headers(args.url, args.cookie)
    scan_mode = args.scan_from is not None
    if args.csv:
        people = list(read_people(args.csv, args.mode))
    elif args.per_id_range:
        people = [
            {"per_id": str(per_id), "query_id": per_id, "mode": args.mode, "per_name": args.per_name or ""}
            for per_id in parse_record_ids(args.per_id_range)
        ]
    elif args.record_ids:
        people = [
            {"per_id": "", "query_id": query_id, "mode": args.mode, "per_name": args.per_name or ""}
            for query_id in parse_record_ids(args.record_ids)
        ]
    else:
        people = [
            {"per_id": "", "query_id": query_id, "mode": args.mode, "per_name": args.per_name or ""}
            for query_id in range(args.scan_from, args.scan_from + args.scan_limit)
        ]

    if not args.apply and not args.check_records:
        print("DRY RUN: no device changes will be sent. Add --apply after --check-records succeeds.")
        print(f"target e_time={e_time}")
        if s_time is not None:
            print(f"target s_time={s_time}")
        if scan_mode:
            last_id = args.scan_from + args.scan_limit - 1
            print(
                f"[DRY RUN] scan query_id={args.scan_from}..{last_id}; "
                f"after first hit, stop after {args.stop_after_misses} consecutive misses"
            )
            return
        for index, person in enumerate(people, start=1):
            label = person["per_id"] or f"record_id={person['query_id']}"
            print(f"[DRY RUN] #{index} {label} query_id={person['query_id']} mode={person['mode']}")
        return

    ok_count = 0
    found_count = 0
    consecutive_misses = 0
    stopped_by_misses = False
    export_rows = []
    for index, person in enumerate(people, start=1):
        per_id = person["per_id"]
        query_id = person["query_id"]
        try:
            record, raw_data = query_face(args.url, headers, query_id, per_id or None)
            if not record:
                print(f"[MISS] #{index} query_id={query_id} per_id={per_id or '<unknown>'} query_face returned no usable face record")
                if scan_mode and found_count > 0:
                    consecutive_misses += 1
                    if consecutive_misses >= args.stop_after_misses:
                        stopped_by_misses = True
                        print(f"[STOP] {consecutive_misses} consecutive missing records after last hit")
                        break
                if args.delay:
                    time.sleep(args.delay)
                continue

            found_count += 1
            consecutive_misses = 0
            record_per_id = str(record.get("per_id") or "")
            target_per_id = per_id or record_per_id
            if not target_per_id:
                print(f"[FAIL] #{index} query_id={query_id} record has no per_id")
                continue

            csv_name = (
                pick_person_name(record)
                or find_person_name(raw_data, target_per_id)
                or person.get("per_name", "").strip()
            )
            present = [
                field
                for field in ("face_id", *PERSON_NAME_FIELDS, "img_data", "s_time", "e_time")
                if field in record
            ]
            print(
                f"[QUERY] #{index} query_id={query_id} per_id={target_per_id} "
                f"name={csv_name or '<missing>'} fields={present} img_len={len(str(record.get('img_data', '')))}"
            )
            export_rows.append(
                {
                    "query_id": query_id,
                    "per_id": target_per_id,
                    "per_name": csv_name,
                    "mode": person["mode"],
                    "face_id": record.get("face_id", ""),
                    "s_time": record.get("s_time", ""),
                    "e_time": record.get("e_time", ""),
                }
            )

            if args.check_records and not args.apply:
                if args.delay:
                    time.sleep(args.delay)
                continue

            update_payload = build_update_payload(record, target_per_id, s_time, e_time, person.get("per_name", ""))
            status, data, text = post_json(args.url, headers, update_payload)
            ensure_ack("update_face_ex", status, data, text)
            print(f"[OK] #{index} per_id={target_per_id} update_face_ex")

            status, data, text = post_json(args.url, headers, build_timeleave_payload(target_per_id, person["mode"]))
            ensure_ack("set_person_timeleave", status, data, text)
            print(f"[OK] #{index} per_id={target_per_id} set_person_timeleave mode={person['mode']}")

            ok_count += 1
            time.sleep(args.delay)
        except Exception as exc:
            print(f"[FAIL] #{index} query_id={query_id} per_id={per_id or '<unknown>'} {exc}")
            break

    if args.export_csv and export_rows:
        with args.export_csv.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=("query_id", "per_id", "per_name", "mode", "face_id", "s_time", "e_time"),
            )
            writer.writeheader()
            writer.writerows(export_rows)
        print(f"Exported checked records: {args.export_csv}")
        missing_names = sum(1 for row in export_rows if not str(row.get("per_name", "")).strip())
        if missing_names:
            print(f"WARNING: {missing_names} exported records have empty per_name; fill them before --apply")

    if scan_mode and not stopped_by_misses:
        last_id = args.scan_from + args.scan_limit - 1
        print(f"Scan stopped at scan limit: query_id={last_id}")

    print(f"Found count: {found_count}")
    print(f"Done. Updated count: {ok_count}")


if __name__ == "__main__":
    main()
