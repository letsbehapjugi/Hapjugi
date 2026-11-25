from __future__ import annotations

import html
import json
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from supabase import Client, create_client

APP_TITLE = "합주기가 됩시다 합(베타)"
APP_URL = "https://hapjugi.streamlit.app/"
SCHEDULE_COMPONENT = components.declare_component(
    "schedule_grid", path=str(Path(__file__).parent / "schedule_grid_component")
)

STATUS_OPTIONS = [
    "합주 완전 가능",
    "합주 가능(애매)",
    "합주 가능(불편)",
    "합주 완전 불가",
]
CLEAR_STATUS = "입력 지우기"
DEFAULT_SESSIONS = ["보컬", "일렉 기타", "베이스", "키보드", "드럼"]
ADD_SESSION_OPTION = "＋ 새 세션 추가"
# Light-mode-first palette; these same colors are used in both app themes.
STATUS_COLORS = {
    STATUS_OPTIONS[0]: "#00C853",  # vivid green
    STATUS_OPTIONS[1]: "#FFD600",  # vivid yellow
    STATUS_OPTIONS[2]: "#FF6D00",  # vivid orange
    STATUS_OPTIONS[3]: "#F44336",  # vivid red
}
STATUS_CLASSES = {
    STATUS_OPTIONS[0]: "fully-available",
    STATUS_OPTIONS[1]: "maybe-available",
    STATUS_OPTIONS[2]: "uncomfortable",
    STATUS_OPTIONS[3]: "unavailable",
}


def get_supabase() -> Client:
    """Return this browser session's Supabase client and keep its auth session alive.

    Streamlit reruns the script frequently. Re-creating the Supabase client and
    exchanging the same Google ID token on every rerun eventually causes
    ``Bad ID token`` after that short-lived Google token expires. Keep one
    client per Streamlit session instead; supabase-py's Auth client retains the
    Supabase refresh token and can refresh its own session.
    """
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_PUBLISHABLE_KEY"]

    client = st.session_state.get("_hapjugi_supabase_client")
    if client is None:
        client = create_client(url, key)
        st.session_state["_hapjugi_supabase_client"] = client

    if not getattr(st.user, "is_logged_in", False):
        return client

    try:
        # Reuse an existing Supabase session. Do NOT exchange the Google ID
        # token again on every Streamlit rerun.
        session = client.auth.get_session()
        if session is not None:
            expires_at = getattr(session, "expires_at", None)
            refresh_token = getattr(session, "refresh_token", None)

            # Refresh shortly before expiry. The SDK also maintains its auth
            # session internally, but this explicit check covers idle tabs.
            if expires_at is not None and int(expires_at) <= int(time.time()) + 60:
                if not refresh_token:
                    raise RuntimeError("Supabase 세션의 Refresh Token이 없습니다.")
                refreshed = client.auth.refresh_session(refresh_token)
                if not getattr(refreshed, "session", None):
                    raise RuntimeError("Supabase 세션 갱신에 실패했습니다.")
            return client

        # First login in this Streamlit browser session only.
        tokens = st.user.tokens
        id_token = tokens.get("id")
        access_token = tokens.get("access")

        if not id_token:
            raise RuntimeError(
                "Google 로그인 토큰을 읽지 못했습니다. Secrets의 "
                '[auth] expose_tokens = ["id", "access"] 설정을 확인하고 다시 로그인해 주세요.'
            )

        credentials = {"provider": "google", "token": id_token}
        if access_token:
            credentials["access_token"] = access_token

        response = client.auth.sign_in_with_id_token(credentials)
        if getattr(response, "session", None):
            return client

        raise RuntimeError("Supabase 로그인 세션을 만들지 못했습니다.")
    except Exception as exc:
        st.error(
            f"Supabase 로그인 연결에 실패했습니다: {exc}"
        )
        st.warning(
            "로그인 세션을 복구하지 못했습니다. Google 로그인을 다시 연결해 주세요."
        )
        if st.button("Google 로그인 다시 연결", type="primary", key="supabase_reauth"):
            st.session_state.pop("_hapjugi_supabase_client", None)
            st.logout()
        st.stop()

    return client


def require_login() -> None:
    if not getattr(st.user, "is_logged_in", False):
        st.title(APP_TITLE)
        st.button("Google로 로그인", type="primary", use_container_width=True, on_click=st.login)
        st.stop()


def current_user_id() -> str:
    # Supabase Auth session is established in get_supabase().
    response = SUPABASE.auth.get_user()
    if not response.user:
        st.error("로그인 정보를 확인하지 못했습니다. 다시 로그인해 주세요.")
        st.stop()
    return response.user.id


def current_user_name() -> str:
    user = st.user.to_dict()
    return str(user.get("name") or user.get("email", "").split("@")[0] or "사용자")


def minute_to_time(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def time_choices(include_midnight_end: bool = False) -> list[str]:
    end = 1440 if include_midnight_end else 1425
    return [minute_to_time(m) for m in range(0, end + 1, 15)]


def time_to_minute(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def get_windows(room: dict[str, Any]) -> list[dict[str, Any]]:
    value = room.get("unavailable_windows") or []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    return [w for w in value if isinstance(w, dict) and "start" in w and "end" in w]


def window_applies(window: dict[str, Any], selected_date: date) -> bool:
    scope = window.get("scope", "all")
    if scope == "all":
        return True
    if scope == "weekdays":
        return selected_date.weekday() < 5
    if scope == "weekends":
        return selected_date.weekday() >= 5
    return selected_date.isoformat() in (window.get("dates") or [])


def is_unavailable(value: str, windows: list[dict[str, Any]], selected_date: date | None = None) -> bool:
    minute = time_to_minute(value)
    for window in windows:
        if selected_date is not None and not window_applies(window, selected_date):
            continue
        start = time_to_minute(window["start"])
        end = time_to_minute(window["end"])
        if start < end and start <= minute < end:
            return True
        if start > end and (minute >= start or minute < end):
            return True
    return False


def week_dates(room: dict[str, Any], selected_date: date) -> list[date]:
    room_start = date.fromisoformat(str(room["start_date"]))
    room_end = date.fromisoformat(str(room["end_date"]))
    week_start = selected_date - timedelta(days=selected_date.weekday())
    first = max(week_start, room_start)
    last = min(week_start + timedelta(days=6), room_end)
    if first > last:
        return []
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def visible_minutes(windows: list[dict[str, Any]], selected_date: date | None = None) -> list[int]:
    return list(range(0, 1440, 15))


def all_room_dates(room: dict[str, Any]) -> list[date]:
    room_start = date.fromisoformat(str(room["start_date"]))
    room_end = date.fromisoformat(str(room["end_date"]))
    return [room_start + timedelta(days=i) for i in range((room_end-room_start).days + 1)]


def effective_windows_for_date(windows: list[dict[str, Any]], selected_date: date) -> list[dict[str, Any]]:
    return [w for w in windows if window_applies(w, selected_date)]


def compact_visible_minutes_for_date(windows: list[dict[str, Any]], selected_date: date) -> list[int]:
    slots = list(range(0, 1440, 15))
    blocked = {m for m in slots if is_unavailable(minute_to_time(m), windows, selected_date)}
    first, last = 0, len(slots) - 1
    while first <= last and slots[first] in blocked:
        first += 1
    while last >= first and slots[last] in blocked:
        last -= 1
    return slots[first:last + 1]



def format_hour_label(minute: int) -> str:
    if minute % 60:
        return ""
    hour = minute // 60
    if hour == 0:
        return "오전 12시"
    if hour < 12:
        return f"오전 {hour}시"
    if hour == 12:
        return "오후 12시"
    return f"오후 {hour - 12}시"


def make_room_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def db_rooms_for_user(user_id: str) -> list[dict[str, Any]]:
    memberships = (
        SUPABASE.table("room_members")
        .select("room_id,name,role")
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )
    if not memberships:
        return []
    room_ids = [m["room_id"] for m in memberships]
    rooms = (
        SUPABASE.table("rooms")
        .select("id,room_code,room_name,host_id,start_date,end_date,unavailable_windows,created_at")
        .in_("id", room_ids)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    by_id = {m["room_id"]: m for m in memberships}
    for room in rooms:
        room["my_membership"] = by_id.get(room["id"], {})
    return rooms


def get_room(room_id: str) -> dict[str, Any] | None:
    result = (
        SUPABASE.table("rooms")
        .select("id,room_code,room_name,host_id,start_date,end_date,unavailable_windows,created_at")
        .eq("id", room_id)
        .maybe_single()
        .execute()
    )
    return result.data


def get_members(room_id: str) -> list[dict[str, Any]]:
    return (
        SUPABASE.table("room_members")
        .select("id,user_id,name,role,joined_at,session,session_order")
        .eq("room_id", room_id)
        .order("joined_at")
        .execute()
        .data
        or []
    )


def session_options(members: list[dict[str, Any]] | None = None) -> list[str]:
    """Default session choices followed by any custom sessions already in this room."""
    members = members or []
    extras = sorted({str(m.get("session") or "").strip() for m in members} - {"", "미지정", *DEFAULT_SESSIONS})
    return DEFAULT_SESSIONS + extras


def assign_member_session(room_id: str, user_id: str, session_name: str) -> None:
    session_name = session_name.strip() or "보컬"
    members = get_members(room_id)
    same_session = [m for m in members if str(m.get("session") or "미지정") == session_name and str(m.get("user_id")) != str(user_id)]
    next_order = max([int(m.get("session_order") or 1) for m in same_session] or [0]) + 1
    SUPABASE.table("room_members").update({"session": session_name, "session_order": next_order}).eq("room_id", room_id).eq("user_id", user_id).execute()


def session_display_map(members: list[dict[str, Any]]) -> dict[str, str]:
    """Assign visible 1-based labels in session order, with join time as tie-breaker."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        name = str(member.get("session") or "미지정")
        grouped.setdefault(name, []).append(member)
    result: dict[str, str] = {}
    for session_name, group in grouped.items():
        group.sort(key=lambda m: (int(m.get("session_order") or 1), str(m.get("joined_at") or "")))
        for index, member in enumerate(group, 1):
            result[str(member.get("user_id"))] = f"{session_name} {index}" if len(group) > 1 else session_name
    return result


def reorder_session_members(room_id: str, members: list[dict[str, Any]], target_user_id: str, new_session: str, new_order: int) -> None:
    """Persist a session choice and compact the order for both affected sessions."""
    target = next((m for m in members if str(m.get("user_id")) == str(target_user_id)), None)
    if not target:
        raise RuntimeError("멤버 정보를 찾지 못했습니다.")
    old_session = str(target.get("session") or "미지정")
    new_session = new_session.strip() or "보컬"
    old_group = [m for m in members if str(m.get("session") or "미지정") == old_session and str(m.get("user_id")) != str(target_user_id)]
    new_group = [m for m in members if str(m.get("session") or "미지정") == new_session and str(m.get("user_id")) != str(target_user_id)]
    if old_session == new_session:
        ordered = sorted([m for m in members if str(m.get("session") or "미지정") == old_session], key=lambda m: (int(m.get("session_order") or 1), str(m.get("joined_at") or "")))
        ordered = [m for m in ordered if str(m.get("user_id")) != str(target_user_id)]
        new_order = max(1, min(int(new_order), len(ordered) + 1))
        ordered.insert(new_order - 1, target)
        for index, member in enumerate(ordered, 1):
            SUPABASE.table("room_members").update({"session_order": index}).eq("room_id", room_id).eq("user_id", member["user_id"]).execute()
        SUPABASE.table("room_members").update({"session": new_session, "session_order": new_order}).eq("room_id", room_id).eq("user_id", target_user_id).execute()
        return
    SUPABASE.table("room_members").update({"session": new_session, "session_order": max(1, min(int(new_order), len(new_group) + 1))}).eq("room_id", room_id).eq("user_id", target_user_id).execute()
    # Compact the old group and keep the destination group ordered after the new member.
    old_ordered = sorted(old_group, key=lambda m: (int(m.get("session_order") or 1), str(m.get("joined_at") or "")))
    for index, member in enumerate(old_ordered, 1):
        SUPABASE.table("room_members").update({"session_order": index}).eq("room_id", room_id).eq("user_id", member["user_id"]).execute()
    refreshed = get_members(room_id)
    destination = [m for m in refreshed if str(m.get("session") or "미지정") == new_session and str(m.get("user_id")) != str(target_user_id)]
    destination.sort(key=lambda m: (int(m.get("session_order") or 1), str(m.get("joined_at") or "")))
    destination.insert(max(0, min(int(new_order) - 1, len(destination))), target)
    for index, member in enumerate(destination, 1):
        SUPABASE.table("room_members").update({"session_order": index}).eq("room_id", room_id).eq("user_id", member["user_id"]).execute()


def session_picker(prefix: str, members: list[dict[str, Any]] | None = None, default: str = "보컬") -> str:
    options = session_options(members) + [ADD_SESSION_OPTION]
    current_options = options[:-1]
    if default not in current_options:
        default = current_options[0]
    choice = st.selectbox("세션", options, index=options.index(default), key=f"{prefix}_session_choice")
    if choice == ADD_SESSION_OPTION:
        custom = st.text_input("추가할 세션 이름", max_chars=30, placeholder="예: 통기타, 바이올린", key=f"{prefix}_session_custom").strip()
        return custom
    return choice


def _paged_availability_query(query, page_size: int = 500) -> list[dict[str, Any]]:
    """Read every matching availability row, regardless of the API row cap."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = query.range(offset, offset + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def get_availability(room_id: str, dates: list[date]) -> list[dict[str, Any]]:
    if not dates:
        return []
    start, end = dates[0].isoformat(), dates[-1].isoformat()
    query = (
        SUPABASE.table("availability")
        .select("id,user_id,date,time,status")
        .eq("room_id", room_id)
        .gte("date", start)
        .lte("date", end)
        .order("date")
        .order("time")
        .order("user_id")
    )
    return _paged_availability_query(query)


def get_user_availability(room_id: str, user_id: str, start: date, end: date) -> list[dict[str, Any]]:
    """Get one member's complete schedule for an inclusive date interval."""
    query = (
        SUPABASE.table("availability")
        .select("date,time,status")
        .eq("room_id", room_id)
        .eq("user_id", user_id)
        .gte("date", start.isoformat())
        .lte("date", end.isoformat())
        .order("date")
        .order("time")
    )
    return _paged_availability_query(query)


def save_availability_rows_verified(
    room_id: str, user_id: str, rows: list[dict[str, Any]], batch_size: int = 100
) -> tuple[int, list[tuple[str, str]]]:
    """Upsert in small batches, fall back per cell, then verify against the DB."""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["date"]), str(row["time"])[:5])
        unique[key] = {
            "room_id": room_id,
            "user_id": user_id,
            "date": key[0],
            "time": key[1],
            "status": row["status"],
        }
    if not unique:
        return 0, []

    row_items = list(unique.items())
    for start_index in range(0, len(row_items), batch_size):
        batch = [row for _, row in row_items[start_index:start_index + batch_size]]
        try:
            SUPABASE.table("availability").upsert(
                batch, on_conflict="room_id,user_id,date,time"
            ).execute()
        except Exception:
            # A failed bulk request should not silently drop the whole stroke.
            # Retry each cell separately so one problematic request cannot hide
            # which individual slots failed.
            for row in batch:
                try:
                    SUPABASE.table("availability").upsert(
                        row, on_conflict="room_id,user_id,date,time"
                    ).execute()
                except Exception:
                    pass

    first_date = date.fromisoformat(min(key[0] for key in unique))
    last_date = date.fromisoformat(max(key[0] for key in unique))
    stored_rows = get_user_availability(room_id, user_id, first_date, last_date)
    stored = {(str(row["date"]), str(row["time"])[:5]): row["status"] for row in stored_rows}
    missing = [key for key, row in unique.items() if stored.get(key) != row["status"]]

    # Repair any slot that was not visible in the confirmation read, then read
    # once more. A cell is not painted in the UI until this final read confirms it.
    for key in missing:
        try:
            SUPABASE.table("availability").upsert(
                unique[key], on_conflict="room_id,user_id,date,time"
            ).execute()
        except Exception:
            pass
    if missing:
        stored_rows = get_user_availability(room_id, user_id, first_date, last_date)
        stored = {(str(row["date"]), str(row["time"])[:5]): row["status"] for row in stored_rows}
        missing = [key for key, row in unique.items() if stored.get(key) != row["status"]]
    return len(unique) - len(missing), missing


def delete_availability_slots_verified(
    room_id: str, user_id: str, slots_by_date: dict[str, set[str]], batch_size: int = 40
) -> list[tuple[str, str]]:
    targets = {(d, t) for d, times in slots_by_date.items() for t in times}
    for date_text, slots in slots_by_date.items():
        ordered = sorted(slots)
        for offset in range(0, len(ordered), batch_size):
            chunk = ordered[offset:offset + batch_size]
            try:
                SUPABASE.table("availability").delete().eq(
                    "room_id", room_id
                ).eq("user_id", user_id).eq("date", date_text).in_("time", chunk).execute()
            except Exception:
                for slot in chunk:
                    try:
                        clear_slot(room_id, user_id, date.fromisoformat(date_text), time_to_minute(slot))
                    except Exception:
                        pass
    if not targets:
        return []
    first_date = date.fromisoformat(min(d for d, _ in targets))
    last_date = date.fromisoformat(max(d for d, _ in targets))
    stored_rows = get_user_availability(room_id, user_id, first_date, last_date)
    still_present = {
        (str(row["date"]), str(row["time"])[:5]) for row in stored_rows
    } & targets
    for date_text, slot in still_present:
        try:
            clear_slot(room_id, user_id, date.fromisoformat(date_text), time_to_minute(slot))
        except Exception:
            pass
    if still_present:
        stored_rows = get_user_availability(room_id, user_id, first_date, last_date)
        still_present = {
            (str(row["date"]), str(row["time"])[:5]) for row in stored_rows
        } & targets
    return sorted(still_present)


def upsert_slot(room_id: str, user_id: str, selected_date: date, minute: int, status: str) -> None:
    SUPABASE.table("availability").upsert(
        {
            "room_id": room_id,
            "user_id": user_id,
            "date": selected_date.isoformat(),
            "time": minute_to_time(minute),
            "status": status,
        },
        on_conflict="room_id,user_id,date,time",
    ).execute()


def clear_slot(room_id: str, user_id: str, selected_date: date, minute: int) -> None:
    SUPABASE.table("availability").delete().match(
        {
            "room_id": room_id,
            "user_id": user_id,
            "date": selected_date.isoformat(),
            "time": minute_to_time(minute),
        }
    ).execute()


def bulk_set(
    room_id: str,
    user_id: str,
    dates: list[date],
    minutes: list[int],
    windows: list[dict[str, Any]],
    status: str,
    existing: set[tuple[str, str]],
) -> int:
    rows = []
    for selected_date in dates:
        for minute in minutes:
            slot = minute_to_time(minute)
            if is_unavailable(slot, windows, selected_date):
                continue
            key = (selected_date.isoformat(), slot)
            if key in existing:
                continue
            rows.append({
                "room_id": room_id,
                "user_id": user_id,
                "date": selected_date.isoformat(),
                "time": slot,
                "status": status,
            })
    if rows:
        saved_count, missing = save_availability_rows_verified(room_id, user_id, rows)
        if missing:
            preview = ", ".join(f"{d} {t}" for d, t in missing[:8])
            raise RuntimeError(f"일부 일정 {len(missing)}칸을 저장하지 못했습니다: {preview}")
        return saved_count
    return 0

def create_room(room_name: str, display_name: str, password: str, start: date, end: date, windows: list[dict[str, Any]], session_name: str = "보컬") -> dict[str, Any]:
    code = make_room_code()
    password_hash_value = None
    if password:
        import bcrypt
        password_hash_value = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    result = SUPABASE.rpc("create_room", {
        "p_room_code": code,
        "p_room_name": room_name,
        "p_host_name": display_name,
        "p_start_date": start.isoformat(),
        "p_end_date": end.isoformat(),
        "p_password_hash": password_hash_value,
    }).execute()
    room_id = result.data[0] if isinstance(result.data, list) else result.data
    if not room_id:
        raise RuntimeError("방 생성에 실패했습니다.")
    SUPABASE.table("rooms").update({"unavailable_windows": windows}).eq("id", room_id).execute()
    assign_member_session(str(room_id), current_user_id(), session_name)
    room = get_room(str(room_id))
    if not room:
        raise RuntimeError("방 생성 후 방 정보를 불러오지 못했습니다.")
    return room


def lookup_room_by_code(code: str) -> dict[str, Any] | None:
    result = SUPABASE.rpc("lookup_room", {"p_room_code": code}).execute()
    if not result.data:
        return None
    return result.data[0] if isinstance(result.data, list) else result.data


def join_room(code: str, password: str, display_name: str, session_name: str = "보컬") -> dict[str, Any]:
    result = SUPABASE.rpc("join_room", {
        "p_room_code": code,
        "p_name": display_name,
        "p_password": password or None,
    }).execute()
    room_id = result.data[0] if isinstance(result.data, list) else result.data
    if not room_id:
        raise RuntimeError("방 참여에 실패했습니다.")
    assign_member_session(str(room_id), current_user_id(), session_name)
    room = get_room(str(room_id))
    if not room:
        raise RuntimeError("방 참여 후 방 정보를 불러오지 못했습니다.")
    return room


def show_status_legend() -> None:
    items = "".join(
        f'<span class="legend-item"><i style="background:{STATUS_COLORS[s]}"></i>{s}</span>'
        for s in STATUS_OPTIONS
    )
    items += '<span class="legend-item"><i class="blocked-swatch"></i>합주 제외</span>'
    st.markdown(f'<div class="legend">{items}</div>', unsafe_allow_html=True)

def render_readonly_grid(dates, minutes, windows, statuses, titles=None) -> None:
    weekday = "월화수목금토일"
    titles = titles or {}
    headers = "".join(f"<th>{d:%m/%d}<br>{weekday[d.weekday()]}</th>" for d in dates)
    rows = []
    for minute in minutes:
        cells = [f'<th class="time">{format_hour_label(minute)}</th>']
        slot = minute_to_time(minute)
        for d in dates:
            key = (d.isoformat(), slot)
            if is_unavailable(slot, windows, d):
                cells.append('<td class="cell blocked" title="합주 제외 시간대"></td>')
            else:
                status = statuses.get(key, "")
                cls = STATUS_CLASSES.get(status, "empty")
                title = titles.get(key, status or "미입력")
                cells.append(f'<td class="cell {cls}" title="{title}"></td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    st.markdown("""
    <style>
    .grid-wrap{overflow:auto;width:100%;border:1px solid var(--hap-border);border-radius:10px;background:var(--hap-bg)}.grid{border-collapse:separate;border-spacing:0;width:100%;min-width:520px;table-layout:fixed;color:var(--hap-text);background:var(--hap-bg)}
    .grid th,.grid td{border-right:1px solid var(--hap-border);border-bottom:1px solid var(--hap-border);padding:0;text-align:center}.grid tr>*:last-child{border-right:0}.grid tbody tr:last-child>*{border-bottom:0}
    .grid thead th{height:38px;background:var(--hap-surface);color:var(--hap-text);position:sticky;top:0;z-index:2}.grid .time{width:78px;min-width:78px;text-align:right;padding-right:7px;background:var(--hap-surface);color:var(--hap-muted);font-size:.72rem}
    .grid .cell{height:16px}.grid .empty{background:var(--hap-bg)}.grid .blocked{background:var(--hap-surface)}.grid .fully-available{background:#00C853}.grid .maybe-available{background:#FFD600}.grid .uncomfortable{background:#FF6D00}.grid .unavailable{background:#F44336}
    .legend{display:flex;flex-wrap:wrap;gap:7px 14px;margin:4px 0 10px;font-size:.82rem}.legend-item{display:inline-flex;align-items:center;gap:5px}.legend-item i{display:inline-block;width:13px;height:13px;border:1px solid #484f58;border-radius:3px}.blocked-swatch{background:#30363d}
    @media(max-width:700px){.grid{min-width:480px}.grid .cell{height:15px}.grid .time{width:64px;min-width:64px;font-size:.65rem}.grid thead th{height:34px;font-size:.75rem}}
    </style>
    """ + f'<div class="grid-wrap"><table class="grid"><thead><tr><th class="time">시간</th>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>', unsafe_allow_html=True)

def render_schedule_component(room, user_id, dates, minutes, windows, statuses, selected_status, editable=True, key="schedule", reset_token=0, cell_colors=None, cell_details=None):
    blocked = {
        f"{d.isoformat()}|{minute_to_time(m)}"
        for d in dates
        for m in minutes
        if is_unavailable(minute_to_time(m), windows, d)
    }

    # Keep the component's optimistic paint in the browser while Python writes
    # to Supabase. The browser clears that temporary layer only after this run
    # returns a matching acknowledgement with freshly-read server data.
    hidden = set()
    for d in dates:
        visible = compact_visible_minutes_for_date(windows, d)
        if visible:
            first_visible, last_visible = min(visible), max(visible)
            for minute in minutes:
                if minute < first_visible or minute > last_visible:
                    hidden.add(f"{d.isoformat()}|{minute_to_time(minute)}")
        else:
            hidden.update(f"{d.isoformat()}|{minute_to_time(minute)}" for minute in minutes)
    hidden_rows = [
        minute_to_time(minute)
        for minute in minutes
        if dates and all(f"{d.isoformat()}|{minute_to_time(minute)}" in hidden for d in dates)
    ]

    event_state_key = f"_last_schedule_event_{key}"
    ack_state_key = f"_schedule_save_ack_{key}"
    save_ack = st.session_state.get(ack_state_key) if editable else None

    result = SCHEDULE_COMPONENT(
        dates=[d.isoformat() for d in dates],
        minutes=minutes,
        statuses={
            f"{d.isoformat()}|{minute_to_time(m)}": statuses.get(
                (d.isoformat(), minute_to_time(m)), ""
            )
            for d in dates
            for m in minutes
        },
        cell_colors=cell_colors or {},
        cell_details=cell_details or {},
        blocked=list(blocked),
        hidden=list(hidden),
        hidden_rows=hidden_rows,
        status_options=STATUS_OPTIONS,
        selected_status=selected_status,
        editable=editable,
        reset_token=reset_token,
        optimistic_ack=save_ack,
        key=key,
        default=None,
    )

    # The acknowledgement is included in the component arguments above; it
    # can now be removed from session state so it is delivered only once.
    if save_ack is not None:
        st.session_state.pop(ack_state_key, None)

    if not (
        editable
        and result
        and result.get("action") == "paint"
        and result.get("event_id")
        and result.get("event_id") != st.session_state.get(event_state_key)
    ):
        return

    status = result.get("status") or selected_status
    rows_to_upsert: list[dict[str, Any]] = []
    clear_by_date: dict[str, set[str]] = {}

    for cell in result.get("cells") or []:
        try:
            selected_date = date.fromisoformat(str(cell["date"]))
            time_text = str(cell["minute"])
            if ":" in time_text:
                hour_text, minute_text = time_text.split(":", 1)
                minute = int(hour_text) * 60 + int(minute_text)
            else:
                minute = int(time_text)
        except (KeyError, TypeError, ValueError):
            continue

        if not 0 <= minute < 1440 or minute % 15 != 0:
            continue
        slot = minute_to_time(minute)
        if is_unavailable(slot, windows, selected_date):
            continue

        if status == CLEAR_STATUS:
            clear_by_date.setdefault(selected_date.isoformat(), set()).add(slot)
        elif status in STATUS_OPTIONS:
            rows_to_upsert.append({
                "room_id": room["id"],
                "user_id": user_id,
                "date": selected_date.isoformat(),
                "time": slot,
                "status": status,
            })

    affected = {
        (str(row["date"]), str(row["time"])[:5]) for row in rows_to_upsert
    }
    affected.update(
        (date_text, slot)
        for date_text, times in clear_by_date.items()
        for slot in times
    )
    if not affected:
        event_id = str(result["event_id"])
        st.session_state[event_state_key] = event_id
        st.session_state[ack_state_key] = {
            "event_id": event_id,
            "failed_cells": [],
            "error": False,
        }
        st.rerun()
        return

    # Persist each operation independently. If only part of a drag fails, the
    # next run reloads the actual database state: committed cells remain and
    # uncommitted cells naturally revert to their prior server values.
    failed_cells: set[tuple[str, str]] = set()
    failure_details: list[str] = []

    if rows_to_upsert:
        try:
            _, missing = save_availability_rows_verified(
                room["id"], user_id, rows_to_upsert, batch_size=100
            )
            failed_cells.update((str(d), str(t)[:5]) for d, t in missing)
        except Exception as exc:
            failed_cells.update(
                (str(row["date"]), str(row["time"])[:5]) for row in rows_to_upsert
            )
            failure_details.append(f"일정 저장 오류: {exc}")

    if clear_by_date:
        try:
            failed_deletes = delete_availability_slots_verified(
                room["id"], user_id, clear_by_date, batch_size=40
            )
            failed_cells.update((str(d), str(t)[:5]) for d, t in failed_deletes)
        except Exception as exc:
            failed_cells.update(
                (date_text, slot)
                for date_text, times in clear_by_date.items()
                for slot in times
            )
            failure_details.append(f"일정 삭제 오류: {exc}")

    # The next run reads the latest committed rows before sending this ack.
    # The iframe then removes its optimistic layer and paints only DB state.
    event_id = str(result["event_id"])
    st.session_state[event_state_key] = event_id
    st.session_state[ack_state_key] = {
        "event_id": event_id,
        "failed_cells": [[d, t] for d, t in sorted(failed_cells)],
        "error": bool(failed_cells or failure_details),
    }

    changed_count = len(affected)
    if failed_cells or failure_details:
        count = len(failed_cells)
        detail = " / ".join(failure_details)
        if count:
            message = (
                f"서버에 저장되지 않은 {count:,}칸은 기존에 서버에 저장된 상태로 되돌렸어요."
            )
            if detail:
                message += f" ({detail})"
        else:
            message = f"저장 결과를 확인하지 못했어요. 서버 상태를 다시 불러왔어요. ({detail})"
        st.session_state[f"_schedule_save_error_{room['id']}"] = message
    elif changed_count:
        st.session_state[f"_schedule_saved_notice_{room['id']}"] = {
            "count": changed_count,
            "action": "delete" if status == CLEAR_STATUS else "save",
        }

    st.rerun()

def _window_editor(prefix: str, room_start: date, room_end: date, windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if "create_window_count" not in st.session_state:
        st.session_state["create_window_count"] = max(1, len(windows))
    count = st.session_state["create_window_count"]
    dates = [room_start + timedelta(days=i) for i in range((room_end-room_start).days + 1)]
    st.markdown("**합주 제외 시간대 설정**")
    enabled = st.checkbox("합주 제외 시간대 설정", value=bool(windows) or count > 0, key=f"{prefix}_enabled")
    if not enabled:
        return []
    if count == 0:
        count = 1
        st.session_state["create_window_count"] = 1
        st.rerun()
    for i in range(count):
        old = windows[i] if i < len(windows) else {"start":"00:00","end":"06:00","scope":"all","dates":[]}
        with st.container(border=True):
            h1,h2 = st.columns([5,1])
            h1.markdown(f"**합주 제외 시간대 {i+1}**")
            if h2.button("삭제", key=f"{prefix}_del_{i}"):
                st.session_state[f"{prefix}_delete"] = i
                st.rerun()
            a,b = st.columns(2)
            options=time_choices(); end_options=time_choices(True)
            s=a.selectbox("시작", options, index=options.index(old.get("start","00:00")), key=f"{prefix}_s_{i}")
            e=b.selectbox("종료", end_options, index=end_options.index(old.get("end","06:00")), key=f"{prefix}_e_{i}")
            scope_map={"all":"전체","weekdays":"주중","weekends":"주말","dates":"날짜 선택"}
            scopes=list(scope_map)
            scope=st.radio("적용 날짜", scopes, index=scopes.index(old.get("scope","all")), format_func=lambda x:scope_map[x], horizontal=True, key=f"{prefix}_scope_{i}")
            chosen=[]
            if scope=="dates":
                chosen=st.multiselect("날짜", dates, default=[date.fromisoformat(x) for x in old.get("dates",[]) if x in {d.isoformat() for d in dates}], format_func=lambda d:f"{d:%m/%d} ({'월화수목금토일'[d.weekday()]})", key=f"{prefix}_dates_{i}")
            st.session_state[f"{prefix}_values_{i}"]={"start":s,"end":e,"scope":scope,"dates":[d.isoformat() for d in chosen]}
    if st.button("+", key=f"{prefix}_add", use_container_width=True):
        st.session_state["create_window_count" if prefix=="create" else f"{prefix}_count"] = count+1
        st.rerun()
    return [st.session_state.get(f"{prefix}_values_{i}", {}) for i in range(count)]


def _normalize_window_editor(prefix: str, count: int) -> None:
    delete = st.session_state.pop(f"{prefix}_delete", None)
    if delete is None:
        return
    for i in range(delete, count-1):
        for suffix in ("s","e","scope","dates","values"):
            old=f"{prefix}_{suffix}_{i+1}"; new=f"{prefix}_{suffix}_{i}"
            if old in st.session_state:
                st.session_state[new]=st.session_state[old]
    st.session_state["create_window_count" if prefix=="create" else f"{prefix}_count"]=max(0,count-1)
    st.rerun()


def show_room_creation(user_id: str):
    with st.container(key="room-create-form"):
        st.subheader("새 합주 방 만들기")
        if "create_window_count" not in st.session_state:
            st.session_state["create_window_count"] = 0
        room_name = st.text_input("방 이름", placeholder="예: 경희대학교 한국어학과 밴드 동아리 ‘가락’ 합주", key="create_room_name")
        host_name = st.text_input("내 이름", value=current_user_name(), key="create_host_name")
        host_session = session_picker("create_host", [], "보컬")
        password = st.text_input("방 비밀번호 (선택)", type="password", help="비워 두면 비밀번호 없이 방 코드만으로 참여할 수 있습니다.", key="create_password")
        c1,c2=st.columns(2)
        start=c1.date_input("합주 시작일", date.today(), key="create_start")
        end=c2.date_input("합주 종료일", date.today()+timedelta(days=13), key="create_end")
        if end < start:
            st.error("종료일은 시작일보다 빠를 수 없습니다.")
            return
        _normalize_window_editor("create", st.session_state.get("create_window_count",0))
        windows=_window_editor("create", start, end, [])
        if st.button("방 만들기", type="primary", use_container_width=True, key="create_room_submit"):
            room_name,host_name=room_name.strip(),host_name.strip()
            if not room_name or not host_name:
                st.error("방 이름과 내 이름을 입력해 주세요."); return
            if not host_session:
                st.error("세션을 선택하거나 새 세션 이름을 입력해 주세요."); return
            valid=[]
            for w in windows:
                if not w: continue
                if w["start"]==w["end"]: st.error("합주 제외 시간대의 시작과 종료를 다르게 설정해 주세요."); return
                if w["scope"]=="dates" and not w["dates"]: st.error("날짜 선택을 사용한 시간대에는 최소 한 날짜를 골라 주세요."); return
                valid.append(w)
            try:
                created=create_room(room_name,host_name,password.strip(),start,end,valid,host_session)
                st.session_state["active_room_id"]=created["id"]
                st.session_state["pending_main_section"]="내 합주실"
                st.success(f"방을 만들었습니다. 방 코드: **{created['room_code']}**")
                st.rerun()
            except Exception as exc:
                st.error(f"방을 만들지 못했습니다: {exc}")


def show_join_room():
    with st.container(key="room-join-form"):
        st.subheader("초대 코드로 방 참여하기")
        code = st.text_input("6자리 방 코드", max_chars=6, placeholder="예: 123456", key="join_room_code")
        password = st.text_input("방 비밀번호", type="password", help="방장이 비밀번호를 설정하지 않았다면 비워 두세요.", key="join_room_password")
        name = st.text_input("내 이름", value=current_user_name(), key="join_room_name")
        found_room = lookup_room_by_code(code.strip()) if len(code.strip()) == 6 and code.strip().isdigit() else None
        existing_members = get_members(str(found_room["id"])) if found_room else []
        session_name = session_picker(f"join_{found_room['id']}" if found_room else "join", existing_members, "보컬")
        if found_room:
            st.caption(f"참여할 방: **{found_room['room_name']}**")
        if st.button("방 참여", type="primary", use_container_width=True, key="join_room_submit"):
            code, name = code.strip(), name.strip()
            if len(code) != 6 or not code.isdigit():
                st.error("방 코드는 숫자 6자리로 입력해 주세요.")
                return
            if not name:
                st.error("내 이름을 입력해 주세요.")
                return
            if not session_name:
                st.error("세션을 선택하거나 새 세션 이름을 입력해 주세요.")
                return
            try:
                room = lookup_room_by_code(code)
                if not room:
                    st.error("해당 코드의 방을 찾을 수 없습니다.")
                    return
                joined = join_room(code, password.strip(), name, session_name)
                st.session_state["active_room_id"] = joined["id"]
                st.session_state["pending_main_section"] = "내 합주실"
                st.success(f"'{room['room_name']}' 방에 참여했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(f"방 코드 또는 비밀번호가 맞지 않거나 세션 정보를 저장하지 못했습니다: {exc}")


@st.dialog("내 일정 전체 지우기", width="small")
def confirm_clear_schedule(room, user_id, dates, notice_key, reset_token_key):
    st.write("정말 지우시겠어요?")
    cancel_col, confirm_col = st.columns(2)
    if cancel_col.button("취소", use_container_width=True, key=f"cancel_schedule_clear_{room['id']}"):
        st.rerun()
    if confirm_col.button("지우기", type="primary", use_container_width=True,
                          key=f"confirm_schedule_clear_{room['id']}"):
        try:
            SUPABASE.table("availability").delete().eq("room_id", room["id"]).eq("user_id", user_id).execute()
            remaining = get_user_availability(room["id"], user_id, dates[0], dates[-1]) if dates else []
            if remaining:
                raise RuntimeError(f"아직 {len(remaining)}칸이 남아 있습니다.")
            st.session_state[reset_token_key] = st.session_state.get(reset_token_key, 0) + 1
            st.session_state[notice_key] = 0
            st.rerun()
        except Exception as exc:
            st.error(f"일정을 전체 삭제하지 못했습니다: {exc}")


def show_schedule_input(room, user_id, windows):
    st.subheader("내 일정 입력")
    dates = all_room_dates(room)
    minutes = visible_minutes(windows)
    selected_status = st.session_state.get(f"paint_status_{room['id']}", STATUS_OPTIONS[0])
    rows = get_availability(room["id"], dates)
    statuses = {(str(r["date"]), str(r["time"])[:5]): r["status"] for r in rows if r["user_id"] == user_id}

    notice_key = f"_schedule_saved_notice_{room['id']}"
    saved_notice = st.session_state.pop(notice_key, None)
    if isinstance(saved_notice, dict):
        notice_count = int(saved_notice.get("count", 0))
        if notice_count and saved_notice.get("action") == "delete":
            st.success(f"{notice_count:,}칸의 일정이 서버에서 삭제되었어요.")
        elif notice_count:
            st.success(f"{notice_count:,}칸의 일정이 서버에 저장되었어요.")
    elif saved_notice:
        st.success(f"{saved_notice:,}칸의 일정이 서버에 저장되었어요.")

    save_error = st.session_state.pop(f"_schedule_save_error_{room['id']}", None)
    if save_error:
        st.error(save_error)

    # 안내 문구를 위쪽 중앙에 두고, 두 일괄 설정 버튼은 그 아래 한 줄로 모은다.
    reset_token_key = f"schedule_reset_{room['id']}"
    mine = {(str(r['date']), str(r['time'])[:5]) for r in rows if r['user_id'] == user_id}
    bulk_col, clear_col = st.columns([2.2, 1], gap="large", vertical_alignment="center")

    with bulk_col:
        with st.container(key=f"bulk-family-{room['id']}", border=True):
            st.markdown(
                '<div class="schedule-tools-heading">미선택 시간 일괄 설정</div>',
                unsafe_allow_html=True,
            )
            # 양쪽 여백 열을 둬서 버튼 두 개가 패널 중앙에 함께 모이게 한다.
            _left_space, yes_col, _button_gap, no_col, _right_space = st.columns(
                [0.55, 0.88, 0.12, 0.88, 0.55], gap="small", vertical_alignment="center"
            )
            with yes_col:
                if st.button(
                    "합주 완전 가능", use_container_width=True,
                    key=f"bulk_yes_{room['id']}"
                ):
                    try:
                        written = bulk_set(
                            room['id'], user_id, dates, minutes, windows,
                            STATUS_OPTIONS[0], mine
                        )
                        st.session_state[notice_key] = written
                        st.rerun()
                    except Exception as exc:
                        st.error(f"일괄 설정을 저장하지 못했습니다: {exc}")
            with no_col:
                if st.button(
                    "합주 완전 불가능", use_container_width=True,
                    key=f"bulk_no_{room['id']}"
                ):
                    try:
                        written = bulk_set(
                            room['id'], user_id, dates, minutes, windows,
                            STATUS_OPTIONS[3], mine
                        )
                        st.session_state[notice_key] = written
                        st.rerun()
                    except Exception as exc:
                        st.error(f"일괄 설정을 저장하지 못했습니다: {exc}")

    # 전체 삭제는 원래의 별도 가로 버튼으로 유지한다.
    with clear_col:
        if st.button(
            "내 일정 전체 지우기", use_container_width=True,
            key=f"bulk_clear_{room['id']}", type="secondary"
        ):
            confirm_clear_schedule(room, user_id, dates, notice_key, reset_token_key)

    if reset_token_key not in st.session_state:
        st.session_state[reset_token_key] = 0
    render_schedule_component(room, user_id, dates, minutes, windows, statuses, selected_status,
                              editable=True, key=f"editable_{room['id']}",
                              reset_token=st.session_state[reset_token_key])
    if st.button("이 일정 복사하기", key=f"copy_schedule_open_{room['id']}", type="secondary"):
        show_copy_schedule_dialog(room, user_id)


@st.dialog("이 일정 복사하기", width="small")
def show_copy_schedule_dialog(source_room: dict[str, Any], user_id: str) -> None:
    rooms = [room for room in db_rooms_for_user(user_id) if str(room["id"]) != str(source_room["id"])]
    if not rooms:
        st.info("일정을 복사할 다른 합주 방이 없습니다. 다른 방에 먼저 참여해 주세요.")
        return

    source_dates = all_room_dates(source_room)
    source_date_set = {d.isoformat() for d in source_dates}
    st.caption("두 방의 날짜가 겹치는 날만 복사합니다. 같은 날짜와 시간대의 일정만 옮기며, 대상 방에 이미 입력된 값은 원본 일정으로 덮어씁니다. 원본에서 미입력인 칸은 대상 방에서 지우지 않습니다.")
    room_ids = [str(room["id"]) for room in rooms]
    room_map = {str(room["id"]): room for room in rooms}
    target_id = st.selectbox(
        "복사할 합주 방", room_ids,
        format_func=lambda rid: f"{room_map[rid]['room_name']} · {room_map[rid]['start_date']}~{room_map[rid]['end_date']}",
        key=f"copy_target_room_{source_room['id']}"
    )
    target_room = room_map[target_id]
    target_dates = all_room_dates(target_room)
    overlap = sorted(source_date_set & {d.isoformat() for d in target_dates})
    if not overlap:
        st.warning("두 합주 방의 일정 기간이 겹치지 않아 복사할 수 없습니다.")
        return

    st.write(f"**복사 범위:** {overlap[0]} ~ {overlap[-1]} ({len(overlap)}일)")
    source_rows = get_user_availability(
        str(source_room["id"]), user_id,
        date.fromisoformat(overlap[0]), date.fromisoformat(overlap[-1])
    )
    source_map = {(str(row["date"]), str(row["time"])[:5]): row["status"] for row in source_rows
                  if str(row["date"]) in set(overlap) and row.get("status") in STATUS_OPTIONS}
    source_windows = get_windows(source_room)
    target_windows = get_windows(target_room)
    to_copy = []
    skipped_source_blocked = 0
    skipped_target_blocked = 0
    for (date_text, time_text), status in source_map.items():
        selected_date = date.fromisoformat(date_text)
        if is_unavailable(time_text, source_windows, selected_date):
            skipped_source_blocked += 1
            continue
        if is_unavailable(time_text, target_windows, selected_date):
            skipped_target_blocked += 1
            continue
        to_copy.append({"date": date_text, "time": time_text, "status": status})

    st.write(f"복사할 입력: **{len(to_copy):,}칸**")
    if skipped_source_blocked:
        st.caption(f"원본 방에서 합주 제외된 {skipped_source_blocked:,}칸은 복사하지 않습니다.")
    if skipped_target_blocked:
        st.caption(f"대상 방의 합주 제외 시간대와 겹치는 {skipped_target_blocked:,}칸은 건너뜁니다.")
    if st.button("겹치는 날짜의 일정 복사", type="primary", use_container_width=True,
                 disabled=not to_copy, key=f"copy_schedule_submit_{source_room['id']}"):
        try:
            saved_count, missing = save_availability_rows_verified(str(target_room["id"]), user_id, to_copy)
            if missing:
                preview = ", ".join(f"{d} {t}" for d, t in missing[:8])
                st.error(f"일부 일정 {len(missing)}칸을 복사하지 못했습니다: {preview}")
            else:
                st.session_state[f"_schedule_saved_notice_{target_room['id']}"] = saved_count
                st.success(f"{saved_count:,}칸을 '{target_room['room_name']}' 방에 복사했어요.")
        except Exception as exc:
            st.error(f"일정을 복사하지 못했습니다: {exc}")

def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(channel))):02X}" for channel in rgb)


def _fixed_color_ramp(anchors: list[str], steps_per_interval: int = 30) -> list[str]:
    """Build a fixed, quantized palette by interpolating between semantic anchors."""
    rgb_anchors = [_hex_rgb(color) for color in anchors]
    palette: list[str] = []
    for segment in range(len(rgb_anchors) - 1):
        left, right = rgb_anchors[segment], rgb_anchors[segment + 1]
        for step in range(steps_per_interval):
            t = step / steps_per_interval
            palette.append(_rgb_hex(tuple(a + (b - a) * t for a, b in zip(left, right))))
    palette.append(anchors[-1].upper())
    return palette


# Fixed palettes with 61 discrete colors: enough nuance without arbitrary RGB averaging.
_AVAILABLE_RAMP = _fixed_color_ramp(["#00C853", "#FFD600", "#FF6D00"])
_ALL_RAMP = _fixed_color_ramp(["#00C853", "#FFD600", "#FF6D00", "#F44336"])


def _blend_status_colors(values: list[str], include_unavailable: bool = True) -> str | None:
    """Return a color from a fixed green-yellow-orange(-red) ramp.

    In the default filter, red/unavailable answers are excluded and the remaining
    score range is 0..2. Thus green + yellow averages to a true midpoint between
    green and yellow (lime), rather than incorrectly landing on yellow.
    """
    rank = {
        STATUS_OPTIONS[0]: 0.0,
        STATUS_OPTIONS[1]: 1.0,
        STATUS_OPTIONS[2]: 2.0,
        STATUS_OPTIONS[3]: 3.0,
    }
    valid = [rank[v] for v in values if v in rank and (include_unavailable or v != STATUS_OPTIONS[3])]
    if not valid:
        return None
    score = sum(valid) / len(valid)
    palette = _ALL_RAMP if include_unavailable else _AVAILABLE_RAMP
    max_score = 3.0 if include_unavailable else 2.0
    index = round((score / max_score) * (len(palette) - 1))
    return palette[max(0, min(len(palette) - 1, index))]


def show_overview(room, members, windows):
    st.subheader("합주 가능 시간")
    mode = st.segmented_control(
        "표시 방식", ["전원 합주 가능 시간", "모두"],
        default="전원 합주 가능 시간", key=f"overview_filter_{room['id']}",
        label_visibility="collapsed", width="stretch",
    ) or "전원 합주 가능 시간"

    # Session filter stays tucked into an icon popover rather than taking permanent space.
    available_sessions = sorted({str(m.get("session") or "미지정") for m in members}, key=lambda x: (DEFAULT_SESSIONS.index(x) if x in DEFAULT_SESSIONS else 99, x))
    filter_key = f"session_filter_initialized_{room['id']}"
    if filter_key not in st.session_state:
        st.session_state[filter_key] = True
        st.session_state[f"session_filter_selected_{room['id']}"] = available_sessions[:]
    selected_saved = st.session_state.get(f"session_filter_selected_{room['id']}", available_sessions[:])
    known_key = f"session_filter_known_{room['id']}"
    known_sessions = st.session_state.get(known_key, available_sessions[:])
    with st.popover("⚙️", help="세션 필터"):
        st.markdown("**표시할 세션 선택**")
        if not available_sessions:
            st.caption("아직 등록된 세션이 없습니다.")
        selected_now = []
        for sess in available_sessions:
            checkbox_key = f"session_filter_{room['id']}_{sess}"
            if checkbox_key not in st.session_state:
                st.session_state[checkbox_key] = (sess in selected_saved) or (sess not in known_sessions)
            if st.checkbox(sess, key=checkbox_key):
                selected_now.append(sess)
        c1, c2 = st.columns(2)
        if c1.button("모두 선택", key=f"session_filter_all_{room['id']}", use_container_width=True):
            for sess in available_sessions:
                st.session_state[f"session_filter_{room['id']}_{sess}"] = True
            st.rerun()
        if c2.button("모두 해제", key=f"session_filter_none_{room['id']}", use_container_width=True):
            for sess in available_sessions:
                st.session_state[f"session_filter_{room['id']}_{sess}"] = False
            st.rerun()
        st.session_state[f"session_filter_selected_{room['id']}"] = selected_now
        st.session_state[known_key] = available_sessions[:]
    selected_sessions = st.session_state.get(f"session_filter_selected_{room['id']}", available_sessions[:])
    filtered_members = [m for m in members if str(m.get("session") or "미지정") in selected_sessions]
    if not selected_sessions:
        st.info("세션 필터에서 한 개 이상의 세션을 선택해 주세요.")

    dates = all_room_dates(room)
    minutes = visible_minutes(windows)
    rows = get_availability(room['id'], dates)
    lookup = {(str(r['date']), str(r['user_id']), str(r['time'])[:5]): r['status'] for r in rows}
    display_map = session_display_map(filtered_members)
    statuses, cell_colors, cell_details = {}, {}, {}
    possible_slots = pending_cells = 0
    for d in dates:
        dk = d.isoformat()
        for minute in minutes:
            slot = minute_to_time(minute)
            key = (dk, slot)
            component_key = f"{dk}|{slot}"
            if is_unavailable(slot, windows, d):
                continue
            values = [lookup.get((dk, str(m['user_id']), slot)) for m in filtered_members]
            received = [v for v in values if v in STATUS_OPTIONS]
            missing = len(filtered_members) - len(received)
            pending_cells += missing
            has_unavailable = STATUS_OPTIONS[3] in received
            all_possible = bool(filtered_members) and missing == 0 and not has_unavailable
            if all_possible:
                possible_slots += 1
            if mode == "전원 합주 가능 시간" and has_unavailable:
                statuses[key] = "__masked__"
            elif selected_sessions:
                mixed = _blend_status_colors(received, include_unavailable=(mode == "모두"))
                if mixed:
                    cell_colors[component_key] = mixed
            details = []
            for member, value in zip(filtered_members, values):
                session_label = display_map.get(str(member.get("user_id")), str(member.get("session") or "미지정"))
                details.append({
                    "name": str(member.get("name") or "이름 없음"),
                    "session_label": session_label,
                    "status": value if value in STATUS_OPTIONS else "",
                })
            cell_details[component_key] = details

    total_minutes = possible_slots * 15
    days_count = max(1, len(dates))
    avg_minutes = round(total_minutes / days_count)
    c1, c2, c3 = st.columns(3)
    c1.metric("합주 가능 총시간", f"{total_minutes//60}시간 {total_minutes%60}분")
    c2.metric("일별 평균 합주 가능 시간", f"{avg_minutes//60}시간 {avg_minutes%60}분")
    c3.metric("미입력", f"{pending_cells}칸")
    render_schedule_component(
        room, "", dates, minutes, windows, statuses, "", editable=False,
        key=f"overview_{room['id']}", cell_colors=cell_colors,
        cell_details=cell_details,
    )

def show_member_schedule(room, members, windows):
    st.subheader("멤버별 일정")
    if not members: return
    display_map = session_display_map(members)
    user_ids = [str(m["user_id"]) for m in members]
    chosen_id = st.selectbox(
        "일정을 확인할 멤버", user_ids,
        format_func=lambda uid: f"{next((m['name'] for m in members if str(m['user_id']) == uid), '이름 없음')} · {display_map.get(uid, '미지정')}",
        key=f"member_choice_{room['id']}",
    )
    member=next(m for m in members if str(m['user_id'])==str(chosen_id))
    dates=all_room_dates(room); minutes=visible_minutes(windows)
    rows=get_availability(room['id'],dates)
    statuses={(str(r['date']),str(r['time'])[:5]):r['status'] for r in rows if r['user_id']==member['user_id']}
    render_schedule_component(room,"",dates,minutes,windows,statuses,"",editable=False,key=f"member_{room['id']}_{member['user_id']}")


def show_host_settings(room, windows):
    st.subheader("방장 설정")
    prefix=f"host_{room['id']}"
    count_key=f"host_window_count_{room['id']}"
    if count_key not in st.session_state: st.session_state[count_key]=len(windows)
    enabled=st.checkbox("합주 제외 시간대 설정",value=bool(windows),key=f"{prefix}_enabled")
    if enabled and st.session_state[count_key] == 0:
        st.session_state[count_key]=1
        st.rerun()
    dates=all_room_dates(room)
    selected=[]
    if enabled:
        for i in range(st.session_state[count_key]):
            old=windows[i] if i<len(windows) else {'start':'00:00','end':'06:00','scope':'all','dates':[]}
            a,b=st.columns(2); opts=time_choices(); eopts=time_choices(True)
            sv=a.selectbox('시작',opts,index=opts.index(old.get('start','00:00')),key=f'{prefix}_s_{i}')
            ev=b.selectbox('종료',eopts,index=eopts.index(old.get('end','06:00')),key=f'{prefix}_e_{i}')
            scopes=['all','weekdays','weekends','dates']; labels={'all':'전체','weekdays':'주중','weekends':'주말','dates':'날짜 선택'}
            scope=st.radio('적용 날짜',scopes,index=scopes.index(old.get('scope','all')),format_func=lambda x:labels[x],horizontal=True,key=f'{prefix}_scope_{i}')
            chosen=st.multiselect('날짜',dates,default=[date.fromisoformat(x) for x in old.get('dates',[]) if x in {d.isoformat() for d in dates}],format_func=lambda d:f'{d:%m/%d} ("월화수목금토일"[d.weekday()])',key=f'{prefix}_dates_{i}') if scope=='dates' else []
            selected.append({'start':sv,'end':ev,'scope':scope,'dates':[d.isoformat() for d in chosen]})
            if st.button('삭제',key=f'{prefix}_del_{i}'):
                st.session_state[count_key]=max(0,st.session_state[count_key]-1)
                st.rerun()
        if st.button('+',key=f'{prefix}_add',use_container_width=False):
            st.session_state[count_key]+=1
            st.rerun()
    if st.button('저장',type='primary',key=f'{prefix}_save',use_container_width=True):
        if any(w['start']==w['end'] for w in selected): st.error('시작과 종료가 같은 시간대가 있습니다.'); return
        if any(w['scope']=='dates' and not w['dates'] for w in selected): st.error('날짜 선택을 사용한 시간대에는 최소 한 날짜를 골라 주세요.'); return
        SUPABASE.table('rooms').update({'unavailable_windows':selected if enabled else []}).eq('id',room['id']).execute(); st.rerun()


def show_my_rooms(user_id: str):
    rooms = db_rooms_for_user(user_id)
    if not rooms:
        st.info("아직 참여한 합주 방이 없습니다. 방을 만들거나 초대 코드로 참여해 주세요.")
        return None

    # Fetch each room's member list once; reuse it for the selected room.
    for room in rooms:
        room["_members"] = get_members(str(room["id"]))

    expanded_key = "_hapjugi_rooms_expanded"
    expanded = bool(st.session_state.get(expanded_key, False))
    ids = [str(r["id"]) for r in rooms]
    current = str(st.session_state.get("active_room_id") or "")
    if current not in ids:
        current = ids[0]
        st.session_state["active_room_id"] = current

    card_width = 320 if expanded else 362
    carousel_key = "room-carousel-expanded" if expanded else "room-carousel"

    # Keep the heading and card row in one layout group so their spacing and
    # horizontal alignment can be controlled together.
    with st.container(key="room-selector-layout", gap="small"):
        title_col, toggle_col = st.columns([8, 1], vertical_alignment="center")
        title_col.markdown(
            '<div class="room-selector-heading">내 합주실</div>',
            unsafe_allow_html=True,
        )
        if toggle_col.button(
            "접기" if expanded else "펼치기",
            use_container_width=False,
            key="toggle_room_list_layout",
        ):
            st.session_state[expanded_key] = not expanded
            st.rerun()

        # This must remain a horizontal Streamlit container. CSS only controls
        # nowrap/scroll in the collapsed state and wrapping in the expanded state.
        with st.container(
            key=carousel_key,
            horizontal=True,
            horizontal_alignment="center",
            vertical_alignment="center",
            gap="medium",
        ):
            for idx, room in enumerate(rooms):
                room_id = str(room["id"])
                is_selected = room_id == current
                members = room.get("_members") or []
                member_count = len(members)
                my_member = next(
                    (member for member in members if str(member.get("user_id")) == str(user_id)),
                    room.get("my_membership", {}),
                )
                my_session = session_display_map(members).get(
                    str(user_id), str(my_member.get("session") or "미지정")
                )
                safe_room_name = html.escape(str(room.get("room_name") or "이름 없는 합주실"))
                safe_session = html.escape(my_session)
                card_key = f"room-card-selected-{idx}" if is_selected else f"room-card-{idx}"

                with st.container(
                    key=card_key,
                    border=True,
                    width=card_width,
                ):
                    st.markdown(
                        f'''<div class="room-card-content">
                            <div class="room-card-name">{safe_room_name}</div>
                            <div class="room-card-meta">
                                <div class="room-card-meta-item">
                                    <span class="room-card-meta-label">멤버</span>
                                    <strong class="room-card-meta-value">{member_count}명</strong>
                                </div>
                                <div class="room-card-meta-item">
                                    <span class="room-card-meta-label">내 세션</span>
                                    <strong class="room-card-meta-value">{safe_session}</strong>
                                </div>
                            </div>
                        </div>''',
                        unsafe_allow_html=True,
                    )
                    # The widget key gives CSS a stable hook to anchor the button
                    # to the card's lower-right corner without relying on DOM order.
                    if st.button(
                        "선택됨" if is_selected else "이동",
                        type="primary" if is_selected else "secondary",
                        disabled=is_selected,
                        use_container_width=True,
                        key=f"room_open_{room_id}",
                    ):
                        st.session_state["active_room_id"] = room_id
                        st.rerun()

    return next(r for r in rooms if str(r["id"]) == current)


@st.dialog("이 방에 참여한 멤버", width="small")
def show_member_list_dialog(room_id, members, current_user_id, host_id):
    st.write(f"총 {len(members)}명")
    display_map = session_display_map(members)
    for member in members:
        is_host = str(member.get("user_id")) == str(host_id)
        role_label = "👑 방장" if is_host else "멤버"
        suffix = " · 나" if str(member.get("user_id")) == str(current_user_id) else ""
        session_label = display_map.get(str(member.get("user_id")), str(member.get("session") or "미지정"))
        st.markdown(f"- **{member['name']}**{suffix} — {role_label} · `{session_label}`")

    me = next((member for member in members if str(member.get("user_id")) == str(current_user_id)), None)
    if me:
        st.divider()
        current_session = str(me.get("session") or "미지정")
        options = session_options(members)
        if current_session not in options:
            options.append(current_session)
        new_name = st.text_input("이름", value=str(me.get("name") or ""), max_chars=40, key=f"edit_name_{room_id}")
        session_choice = st.selectbox("세션", options + [ADD_SESSION_OPTION], index=options.index(current_session) if current_session in options else 0, key=f"edit_session_{room_id}")
        new_session = st.text_input("새 세션 이름", max_chars=30, placeholder="예: 통기타, 바이올린", key=f"edit_custom_session_{room_id}") if session_choice == ADD_SESSION_OPTION else session_choice
        same = [m for m in members if str(m.get("session") or "미지정") == str(new_session) and str(m.get("user_id")) != str(current_user_id)]
        max_order = len(same) + 1
        old_order = int(me.get("session_order") or 1)
        order_options = list(range(1, max_order + 1))
        default_order = min(old_order if str(new_session) == current_session else max_order, max_order)
        order_key = f"edit_session_order_{room_id}"
        if st.session_state.get(order_key) not in order_options:
            st.session_state[order_key] = default_order
        new_order = st.selectbox("같은 세션 내 순서", order_options, index=order_options.index(st.session_state[order_key]), format_func=lambda n: f"{n}번", key=order_key)
        if st.button("정보 수정", use_container_width=True, key=f"save_member_info_{room_id}"):
            cleaned = new_name.strip()
            if not cleaned:
                st.error("이름을 한 글자 이상 입력해 주세요.")
            elif not str(new_session).strip():
                st.error("세션을 선택하거나 새 세션 이름을 입력해 주세요.")
            else:
                try:
                    SUPABASE.table("room_members").update({"name": cleaned}).eq("room_id", room_id).eq("user_id", current_user_id).execute()
                    reorder_session_members(room_id, members, current_user_id, str(new_session), int(new_order))
                    st.success("이름과 세션 정보를 수정했어요.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"정보를 수정하지 못했습니다: {exc}")

    if str(current_user_id) == str(host_id):
        others = [m for m in members if str(m.get("user_id")) != str(host_id)]
        st.divider()
        st.markdown("**방장 양도**")
        if not others:
            st.caption("방장 권한을 넘길 다른 멤버가 없습니다.")
        else:
            target = st.selectbox("새 방장", others, format_func=lambda m: f"{m.get('name', '이름 없음')} · {display_map.get(str(m.get('user_id')), m.get('session', '미지정'))}", key=f"transfer_target_{room_id}")
            if st.button("방장 권한 양도", type="primary", use_container_width=True, key=f"transfer_host_{room_id}"):
                try:
                    SUPABASE.rpc("transfer_room_host", {"p_room_id": room_id, "p_new_host_id": target["user_id"]}).execute()
                    st.success(f"{target.get('name', '선택한 멤버')} 님에게 방장 권한을 양도했어요.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"방장 권한을 양도하지 못했습니다. SQL 마이그레이션 적용 여부를 확인해 주세요. ({exc})")

def show_active_room(user_id: str):
    room=show_my_rooms(user_id)
    if not room: return
    members = room.get("_members")
    if members is None:
        members = get_members(room['id'])
    me=next((m for m in members if str(m['user_id'])==str(user_id)),None)
    if not me: st.error('이 방의 멤버 정보를 찾지 못했습니다.'); return
    windows=get_windows(room)
    title, member_action, leave_action = st.columns([5, 1.5, 1])
    title.subheader(room['room_name'])
    title.caption(f"방 코드 **{room['room_code']}** · 내 이름 **{me['name']}** · 세션 **{session_display_map(members).get(str(user_id), me.get('session', '미지정'))}**")
    if member_action.button(f"멤버 {len(members)}명", use_container_width=True, key=f"show_members_{room['id']}"):
        show_member_list_dialog(room['id'], members, user_id, room.get("host_id"))
    if leave_action.button('방 나가기', use_container_width=True):
        SUPABASE.table('room_members').delete().eq('room_id',room['id']).eq('user_id',user_id).execute(); st.session_state.pop('active_room_id',None); st.rerun()
    is_host=str(room['host_id'])==str(user_id)
    tabs=st.tabs(['내 일정 입력','합주 가능 시간','멤버별 일정']+(['방장 설정'] if is_host else []))
    with tabs[0]: show_schedule_input(room,user_id,windows)
    with tabs[1]: show_overview(room,members,windows)
    with tabs[2]: show_member_schedule(room,members,windows)
    if is_host:
        with tabs[3]: show_host_settings(room,windows)


def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")

    # Read the actual appearance selected in Streamlit's settings menu.
    # Streamlit 1.50+ exposes this as st.context.theme.type.
    try:
        active_theme = str(st.context.theme.type).lower()
    except Exception:
        active_theme = "light"

    # Streamlit may report the previous appearance on the very first run of a
    # browser session. One lightweight rerun lets the active setting settle.
    if not st.session_state.get("_hapjugi_theme_initialized", False):
        st.session_state["_hapjugi_theme_initialized"] = True
        st.rerun()

    is_dark_theme = active_theme == "dark"

    if is_dark_theme:
        theme_palette_css = """
        <style>
        :root {
          color-scheme: dark !important;
          --hap-bg: var(--st-background-color, #0E1117);
          --hap-surface: var(--st-secondary-background-color, #1B2230);
          --hap-text: var(--st-text-color, #F1F5F9);
          --hap-muted: color-mix(in srgb, var(--hap-text) 68%, var(--hap-surface));
          --hap-border: var(--st-border-color, rgba(226,232,240,.14));
          --hap-panel-gradient: linear-gradient(145deg, var(--hap-surface), color-mix(in srgb, var(--hap-surface) 82%, var(--hap-bg)));
          --hap-button-gradient: linear-gradient(145deg, color-mix(in srgb, var(--hap-surface) 92%, #FFFFFF), var(--hap-surface));
          --hap-shadow-raised: 5px 5px 14px rgba(0,0,0,.42), -3px -3px 10px rgba(255,255,255,.025);
          --hap-shadow-inset: inset 2px 2px 5px rgba(0,0,0,.42), inset -2px -2px 5px rgba(255,255,255,.025);
          --hap-heading: var(--hap-text);
          --hap-button-text: var(--hap-text);
          --hap-chip-bg: color-mix(in srgb, #2563EB 20%, var(--hap-surface));
          --hap-chip-text: var(--hap-text);
          --hap-track: color-mix(in srgb, var(--hap-surface) 82%, var(--hap-bg));
        }
        </style>
        """
    else:
        theme_palette_css = """
        <style>
        :root {
          color-scheme: light !important;
          --hap-bg: var(--st-background-color, #F5F7FB);
          --hap-surface: var(--st-secondary-background-color, #FFFFFF);
          --hap-text: var(--st-text-color, #1F2937);
          --hap-muted: color-mix(in srgb, var(--hap-text) 62%, var(--hap-surface));
          --hap-border: var(--st-border-color, color-mix(in srgb, var(--hap-text) 16%, transparent));
          --hap-panel-gradient: linear-gradient(145deg, var(--hap-surface), color-mix(in srgb, var(--hap-surface) 88%, var(--hap-bg)));
          --hap-button-gradient: linear-gradient(145deg, var(--hap-surface), color-mix(in srgb, var(--hap-surface) 82%, var(--hap-bg)));
          --hap-shadow-raised: 5px 5px 14px rgba(31,41,55,.13), -4px -4px 12px rgba(255,255,255,.72);
          --hap-shadow-inset: inset 2px 2px 5px rgba(31,41,55,.09), inset -2px -2px 5px rgba(255,255,255,.75);
          --hap-heading: var(--hap-text);
          --hap-button-text: var(--hap-text);
          --hap-chip-bg: color-mix(in srgb, #2563EB 8%, var(--hap-surface));
          --hap-chip-text: var(--hap-text);
          --hap-track: color-mix(in srgb, var(--hap-surface) 92%, var(--hap-bg));
        }
        </style>
        """
    st.markdown(theme_palette_css, unsafe_allow_html=True)
    # Keep Streamlit's running/stale state from dimming the app during component events.
    st.markdown("""<style>
    [data-stale], [data-testid="stAppViewContainer"], [data-testid="stAppViewBlockContainer"],
    [data-testid="stMain"], [data-testid="stMainBlockContainer"], section.main, section.main > div {
      opacity: 1 !important; filter: none !important;
    }
    [data-stale] *, [aria-busy="true"] { opacity: 1 !important; filter: none !important; }
    [data-testid="stHeaderActionElements"], a[data-testid="stHeaderActionElements"],
    [data-testid="stMarkdownContainer"] h1 a, [data-testid="stMarkdownContainer"] h2 a,
    [data-testid="stMarkdownContainer"] h3 a { display:none !important; visibility:hidden !important; }

    :root, body, .stApp, [data-testid="stApp"], [data-testid="stAppViewContainer"] {
      --primary-color: #2563EB !important; --color-primary: #2563EB !important;
      --st-primary-color: #2563EB !important; --accent-color: #2563EB !important;
      --primary: #2563EB !important; --primary-50: #EFF6FF !important;
      --primary-100: #DBEAFE !important; --primary-600: #2563EB !important;
      --primary-700: #1D4ED8 !important; --color-primary-light: #DBEAFE !important;
      --color-primary-dark: #1D4ED8 !important; --st-color-primary: #2563EB !important;
      --st-color-primary-bg: #EFF6FF !important; --st-color-primary-hover: #1D4ED8 !important;
      --st-color-primary-active: #1E40AF !important;

      /* Surfaces are supplied by the mode-aware palette injected before this stylesheet. */
    }
    html, body, .stApp, [data-testid="stApp"],
    [data-testid="stAppViewContainer"], [data-testid="stMain"],
    [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"],
    [data-testid="stAppViewBlockContainer"] > div, section.main {
      background-color: var(--hap-bg) !important;
      background: var(--hap-bg) !important;
      color: var(--hap-text) !important;
    }
    [data-testid="stHeader"] {
      background: var(--hap-bg) !important;
    }
    [data-testid="stVerticalBlock"], [data-testid="stHorizontalBlock"] {
      color: var(--hap-text);
    }

    /* Main brand and navigation share the exact viewport center line. */
    .app-brand-title {
      width:100%; box-sizing:border-box; text-align:center; font-size:clamp(1.65rem, 3vw, 2.25rem);
      font-weight:800; letter-spacing:-.055em; color:var(--hap-heading);
      padding:.28rem 0 .78rem; margin:0 auto;
    }
    div.st-key-main_section_radio {
      display:flex !important; justify-content:center !important; align-items:center !important;
      width:100% !important; max-width:100% !important; margin:0 auto 1.35rem !important;
    }
    div.st-key-main_section_radio [data-testid="stSegmentedControl"] {
      width:max-content !important; max-width:100% !important; margin:0 auto !important;
    }
    div.st-key-main_section_radio [data-testid="stSegmentedControl"] > div,
    div.st-key-main_section_radio [data-baseweb="button-group"] {
      display:flex !important; width:max-content !important; max-width:100% !important;
      justify-content:center !important; margin-left:auto !important; margin-right:auto !important;
      border-radius:14px; padding:4px;
      background:var(--hap-track) !important; box-shadow:var(--hap-shadow-inset) !important;
    }
    div.st-key-main_section_radio [data-testid="stSegmentedControl"] button {
      flex:0 0 auto !important; min-width:104px !important;
      justify-content:center; font-weight:650; border-radius:10px !important;
    }

    /* Global action color is blue; timetable status colors remain semantic. */
    button[kind="primary"], [data-testid="stBaseButton-primary"],
    [data-testid="stFormSubmitButton"] button {
      background:#2563EB !important; border-color:#2563EB !important; color:#fff !important;
    }
    button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover,
    [data-testid="stFormSubmitButton"] button:hover {
      background:#1D4ED8 !important; border-color:#1D4ED8 !important;
    }
    /* Force Streamlit and BaseWeb interactive states to use the blue accent. */
    :root, body, .stApp, [data-testid="stApp"],
    [data-testid="stAppViewContainer"], [data-testid="stMain"] {
      --primary-color:#2563EB !important;
      --st-primary-color:#2563EB !important;
      --color-primary:#2563EB !important;
      --primary-50:#EFF6FF !important;
      --primary-100:#DBEAFE !important;
      --primary-600:#2563EB !important;
      --primary-700:#1D4ED8 !important;
      --color-primary-light:#DBEAFE !important;
      --color-primary-dark:#1D4ED8 !important;
      --st-color-primary:#2563EB !important;
      --st-color-primary-bg:#EFF6FF !important;
      --st-color-primary-hover:#1D4ED8 !important;
      --st-color-primary-active:#1E40AF !important;
    }

    /* Selected segmented-control buttons and their hover/focus states. */
    [data-testid="stSegmentedControl"] button[aria-pressed="true"],
    [data-testid="stSegmentedControl"] button[aria-checked="true"],
    [data-testid="stSegmentedControl"] button[aria-selected="true"],
    [data-testid="stSegmentedControl"] button[data-selected="true"],
    [data-testid="stSegmentedControl"] button[data-state="checked"],
    [data-testid="stBaseButton-segmented_controlActive"],
    [data-testid="stBaseButton-segmented_controlActive"]:hover,
    [data-testid="stBaseButton-segmented_controlActive"]:focus,
    [data-testid="stBaseButton-segmented_controlActive"]:focus-visible,
    [data-baseweb="button-group"] button[kind="segmented_controlActive"],
    [data-baseweb="button-group"] button[kind="segmented_controlActive"]:hover,
    div.st-key-main_section_radio [aria-pressed="true"],
    div.st-key-main_section_radio [aria-checked="true"],
    div.st-key-main_section_radio [aria-selected="true"],
    div.st-key-main_section_radio [data-selected="true"],
    div.st-key-main_section_radio [data-state="checked"] {
      background:#2563EB !important;
      background-color:#2563EB !important;
      border-color:#2563EB !important;
      color:#FFFFFF !important;
      box-shadow:0 1px 3px rgba(37,99,235,.24) !important;
    }
    [data-testid="stSegmentedControl"] button:hover:not([aria-pressed="true"]):not([aria-checked="true"]),
    [data-testid="stSegmentedControl"] button:focus-visible:not([aria-pressed="true"]),
    [data-baseweb="button-group"] button:hover:not([kind="segmented_controlActive"]),
    div.st-key-main_section_radio button:hover:not([aria-pressed="true"]):not([aria-checked="true"]) {
      color:#1D4ED8 !important;
      background:color-mix(in srgb, #2563EB 10%, var(--hap-surface)) !important;
      background-color:color-mix(in srgb, #2563EB 10%, var(--hap-surface)) !important;
      border-color:color-mix(in srgb, #2563EB 38%, var(--hap-border)) !important;
    }

    /* Tabs: the moving highlight is separate from the full-width bottom track. */
    .stTabs [data-baseweb="tab"],
    [data-testid="stTabs"] [role="tab"] {
      transition:color .14s ease, background-color .14s ease, border-color .14s ease !important;
    }
    .stTabs [data-baseweb="tab"]:hover,
    .stTabs [role="tab"]:hover,
    [data-testid="stTabs"] [role="tab"]:hover {
      color:#1D4ED8 !important;
      background:color-mix(in srgb, #2563EB 7%, var(--hap-surface)) !important;
      background-color:color-mix(in srgb, #2563EB 7%, var(--hap-surface)) !important;
    }
    .stTabs [data-baseweb="tab"][aria-selected="true"],
    .stTabs [role="tab"][aria-selected="true"],
    [data-testid="stTabs"] [role="tab"][aria-selected="true"],
    [data-testid="stTabs"] [data-baseweb="tab"][aria-selected="true"] {
      color:#2563EB !important;
      border-bottom-color:#2563EB !important;
    }
    .stTabs [data-baseweb="tab-highlight"],
    [data-testid="stTabs"] [data-baseweb="tab-highlight"],
    div[data-baseweb="tab-highlight"] {
      background:#2563EB !important;
      background-color:#2563EB !important;
      border-color:#2563EB !important;
    }
    .stTabs [data-baseweb="tab-border"],
    [data-testid="stTabs"] [data-baseweb="tab-border"],
    div[data-baseweb="tab-border"] {
      background:var(--hap-border) !important;
      background-color:var(--hap-border) !important;
    }
    .stTabs [data-baseweb="tab-highlight"]::after,
    .stTabs [data-baseweb="tab-highlight"]::before {
      background:#2563EB !important;
      border-color:#2563EB !important;
    }
    .stTabs [role="tab"][aria-selected="true"]::after,
    [data-testid="stTabs"] [role="tab"][aria-selected="true"]::after {
      background:#2563EB !important;
      border-color:#2563EB !important;
    }

    /* Dropdown, multiselect, date-picker and calendar hover/active states. */
    [data-baseweb="menu"] [role="option"]:hover,
    [data-baseweb="menu"] [role="option"][aria-selected="true"],
    [data-baseweb="popover"] [role="option"]:hover,
    [data-baseweb="popover"] [role="option"][aria-selected="true"],
    [data-testid="stSelectboxVirtualDropdown"] [role="option"]:hover,
    [data-testid="stMultiSelect"] [role="option"]:hover {
      background:color-mix(in srgb, #2563EB 12%, var(--hap-surface)) !important;
      background-color:color-mix(in srgb, #2563EB 12%, var(--hap-surface)) !important;
      color:var(--hap-text) !important;
    }
    [data-baseweb="calendar"] [aria-selected="true"],
    [data-baseweb="calendar"] button[aria-pressed="true"],
    [data-baseweb="calendar"] button[aria-selected="true"] {
      background:#2563EB !important;
      background-color:#2563EB !important;
      color:#FFFFFF !important;
    }
    [data-baseweb="calendar"] button:hover:not([aria-selected="true"]),
    [data-baseweb="calendar"] [role="button"]:hover:not([aria-selected="true"]) {
      background:color-mix(in srgb, #2563EB 12%, var(--hap-surface)) !important;
      color:#1D4ED8 !important;
    }

    /* Checkboxes, radio controls, sliders and focused inputs. */
    [data-testid="stCheckbox"] input:checked + div,
    [data-testid="stRadio"] input:checked + div,
    [data-testid="stCheckbox"] label:has(input:checked) > div,
    [data-testid="stRadio"] label:has(input:checked) > div,
    [data-baseweb="checkbox"] [aria-checked="true"],
    [data-baseweb="radio"] [aria-checked="true"] {
      border-color:#2563EB !important;
      background-color:#2563EB !important;
      accent-color:#2563EB !important;
    }
    [data-testid="stCheckbox"] label:hover,
    [data-testid="stRadio"] label:hover,
    [data-baseweb="checkbox"]:hover,
    [data-baseweb="radio"]:hover {
      color:#1D4ED8 !important;
    }
    [data-testid="stSlider"] [role="slider"],
    [data-testid="stSlider"] [role="slider"]:focus,
    [data-testid="stSlider"] [role="slider"]:focus-visible {
      border-color:#2563EB !important;
      box-shadow:0 0 0 2px color-mix(in srgb, #2563EB 22%, transparent) !important;
    }
    [data-baseweb="select"]:focus-within,
    [data-baseweb="input"]:focus-within,
    [data-baseweb="textarea"]:focus-within,
    [data-testid="stTextInput"] input:focus,
    [data-testid="stDateInput"] input:focus {
      border-color:#2563EB !important;
      box-shadow:0 0 0 1px #2563EB !important;
    }
    button:focus-visible, [role="tab"]:focus-visible {
      outline-color:#2563EB !important;
    }
    a, [data-testid="stMarkdownContainer"] a { color:#2563EB; }
    [data-baseweb="checkbox"] [aria-checked="true"],
    [data-baseweb="radio"] [aria-checked="true"] {
      border-color:#2563EB !important;
    }

    /* Light-touch neumorphism follows the active theme's own surfaces. */
    [data-testid="stVerticalBlockBorderWrapper"] {
      border:1px solid var(--hap-border) !important; border-radius:16px !important;
      background:var(--hap-panel-gradient) !important; box-shadow:var(--hap-shadow-raised) !important;
    }
    [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
      border-radius:11px !important; transition:box-shadow .16s ease, transform .16s ease, background .16s ease;
    }
    [data-testid="stBaseButton-secondary"] {
      background:var(--hap-button-gradient) !important; border:1px solid var(--hap-border) !important;
      box-shadow:var(--hap-shadow-raised) !important; color:var(--hap-button-text) !important;
    }
    [data-testid="stBaseButton-secondary"]:hover {
      box-shadow:var(--hap-shadow-inset) !important; border-color:rgba(37,99,235,.45) !important;
      color:#2563EB !important;
    }
    [data-testid="stBaseButton-primary"] { box-shadow:3px 3px 8px rgba(37,99,235,.18) !important; }
    [data-testid="stButton"] button:hover:not(:disabled),
    [data-testid="stFormSubmitButton"] button:hover:not(:disabled),
    [data-testid="stDownloadButton"] button:hover:not(:disabled),
    [data-testid="stBaseButton-secondary"]:hover:not(:disabled) {
      border-color:#2563EB !important;
      outline-color:color-mix(in srgb, #2563EB 35%, transparent) !important;
    }
    [data-testid="stButton"] button:focus-visible:not(:disabled),
    [data-testid="stFormSubmitButton"] button:focus-visible:not(:disabled),
    [data-testid="stDownloadButton"] button:focus-visible:not(:disabled) {
      outline:2px solid #2563EB !important;
      outline-offset:2px !important;
      box-shadow:0 0 0 2px color-mix(in srgb, #2563EB 16%, transparent) !important;
    }

    .room-current-state {
      text-align:center; padding:.48rem .35rem; border-radius:10px;
      color:var(--hap-chip-text); font-weight:700; font-size:.88rem;
      background:var(--hap-chip-bg); border:1px solid var(--hap-border);
      box-shadow:var(--hap-shadow-inset);
    }
    .room-card-title { font-size:1.05rem; font-weight:750; line-height:1.35; color:var(--hap-text); }

    /* Bulk actions: centered heading above a compact horizontal pair of buttons. */
    div[class*="st-key-bulk-family-"] [data-testid="stVerticalBlockBorderWrapper"] {
      width:100% !important; max-width:840px !important;
      margin-left:auto !important; margin-right:0 !important;
      min-height:116px !important; box-sizing:border-box !important;
      display:flex !important; align-items:center !important; justify-content:center !important;
      padding:13px 14px 12px !important; border-radius:14px !important;
    }
    div[class*="st-key-bulk-family-"] [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"] {
      width:100% !important; min-height:0 !important; justify-content:center !important;
      align-items:stretch !important; gap:0 !important;
    }
    div[class*="st-key-bulk-family-"] [data-testid="stHorizontalBlock"] {
      width:100% !important; align-items:center !important; justify-content:center !important;
      gap:10px !important;
    }
    div[class*="st-key-bulk-family-"] [data-testid="stBaseButton-secondary"] {
      max-width:100% !important; min-width:0 !important;
    }
    .schedule-tools-heading {
      display:flex; align-items:center; justify-content:center;
      width:100%; min-height:28px; margin:0 0 12px !important; padding:0 7px;
      color:var(--hap-text); font-size:.92rem; font-weight:750;
      letter-spacing:-.03em; text-align:center; line-height:1.3;
      white-space:normal; word-break:keep-all;
    }
    div[class*="st-key-bulk-family-"] [data-testid="stBaseButton-secondary"],
    div[class*="st-key-bulk_clear_"] [data-testid="stBaseButton-secondary"] {
      width:100% !important; min-height:48px !important; height:48px !important;
      padding:.45rem .65rem !important; white-space:nowrap !important;
      line-height:1.2 !important; font-size:.9rem !important;
    }
    div[class*="st-key-bulk_clear_"] [data-testid="stButton"] {
      display:flex !important; justify-content:stretch !important; width:100% !important;
    }

    /* Main content spacing: the title sits near the page top, with a deliberate gap before cards. */
    section.main > div.block-container,
    [data-testid="stMainBlockContainer"] {
      padding-top:1rem !important;
      padding-left:clamp(1rem, 2vw, 1.5rem) !important;
      padding-right:clamp(1rem, 2vw, 1.5rem) !important;
    }

    div.st-key-room-selector-layout {
      width:100% !important;
      max-width:100% !important;
      margin:0 !important;
      padding:0 !important;
    }
    div.st-key-room-selector-layout > [data-testid="stVerticalBlock"],
    div.st-key-room-selector-layout [data-testid="stVerticalBlock"] {
      gap:.7rem !important;
    }
    .room-selector-heading {
      display:flex !important;
      align-items:center !important;
      min-height:32px !important;
      margin:0 !important;
      padding:0 !important;
      color:var(--hap-heading) !important;
      font-size:1.35rem !important;
      font-weight:800 !important;
      line-height:1.25 !important;
      letter-spacing:-.04em !important;
    }
    div.st-key-room-selector-layout [data-testid="stHorizontalBlock"]:first-child {
      padding:0 4px !important;
      margin:0 !important;
      box-sizing:border-box !important;
      align-items:center !important;
    }
    div.st-key-toggle_room_list_layout {
      display:flex !important;
      justify-content:flex-end !important;
      align-items:center !important;
    }
    div.st-key-toggle_room_list_layout [data-testid="stBaseButton-secondary"] {
      width:auto !important;
      min-width:72px !important;
      min-height:32px !important;
      height:32px !important;
      padding:.25rem .7rem !important;
      font-size:.82rem !important;
      border-radius:999px !important;
    }

    /* Only the room list scrolls horizontally; each card has no scrollable area. */
    div.st-key-room-carousel {
      width:100% !important;
      max-width:100% !important;
      margin:0 !important;
      padding:0 4px 14px !important;
      box-sizing:border-box !important;
      overflow-x:auto !important;
      overflow-y:hidden !important;
      scrollbar-width:thin !important;
      overscroll-behavior-x:contain !important;
      touch-action:pan-x pan-y !important;
    }
    div.st-key-room-carousel[data-testid="stHorizontalBlock"],
    div.st-key-room-carousel > [data-testid="stHorizontalBlock"] {
      display:flex !important;
      flex-direction:row !important;
      flex-wrap:nowrap !important;
      align-items:stretch !important;
      justify-content:flex-start !important;
      gap:16px !important;
      width:max-content !important;
      min-width:100% !important;
      max-width:none !important;
      overflow:visible !important;
      box-sizing:border-box !important;
    }
    div.st-key-room-carousel-expanded {
      width:100% !important;
      max-width:100% !important;
      margin:0 !important;
      padding:0 4px 12px !important;
      box-sizing:border-box !important;
      overflow:visible !important;
    }
    div.st-key-room-carousel-expanded[data-testid="stHorizontalBlock"],
    div.st-key-room-carousel-expanded > [data-testid="stHorizontalBlock"] {
      display:flex !important;
      flex-direction:row !important;
      flex-wrap:wrap !important;
      align-items:stretch !important;
      justify-content:flex-start !important;
      gap:16px !important;
      width:100% !important;
      max-width:100% !important;
      overflow:visible !important;
      box-sizing:border-box !important;
    }

    /* Card dimensions are fixed here, not by a height-limited Streamlit container. */
    div[class*="st-key-room-card-"] {
      position:relative !important;
      display:block !important;
      flex:0 0 362px !important;
      width:362px !important;
      min-width:362px !important;
      max-width:362px !important;
      height:224px !important;
      min-height:224px !important;
      max-height:224px !important;
      overflow:visible !important;
      box-sizing:border-box !important;
      scroll-snap-align:start !important;
    }
    div[class*="st-key-room-card-"] > [data-testid="stVerticalBlockBorderWrapper"] {
      position:relative !important;
      display:block !important;
      width:100% !important;
      min-width:100% !important;
      max-width:100% !important;
      height:100% !important;
      min-height:100% !important;
      max-height:100% !important;
      margin:0 !important;
      padding:0 !important;
      box-sizing:border-box !important;
      overflow:hidden !important;
      border-radius:18px !important;
      background:linear-gradient(145deg, var(--hap-surface), color-mix(in srgb, var(--hap-surface) 90%, #2563EB)) !important;
      border:1px solid color-mix(in srgb, #2563EB 22%, var(--hap-border)) !important;
      box-shadow:var(--hap-shadow-raised) !important;
      transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease;
    }
    div[class*="st-key-room-card-selected-"] > [data-testid="stVerticalBlockBorderWrapper"] {
      border:1.5px solid color-mix(in srgb, #2563EB 58%, var(--hap-border)) !important;
      background:linear-gradient(145deg, color-mix(in srgb, #2563EB 7%, var(--hap-surface)), var(--hap-surface)) !important;
      box-shadow:0 8px 22px color-mix(in srgb, #2563EB 14%, transparent), var(--hap-shadow-raised) !important;
    }
    div[class*="st-key-room-card-"] > [data-testid="stVerticalBlockBorderWrapper"] > [data-testid="stVerticalBlock"] {
      position:relative !important;
      display:block !important;
      width:100% !important;
      min-width:0 !important;
      height:100% !important;
      min-height:0 !important;
      max-height:100% !important;
      margin:0 !important;
      padding:0 !important;
      box-sizing:border-box !important;
      overflow:hidden !important;
    }

    /* Content occupies only the upper portion; it cannot push or overlap the button. */
    div[class*="st-key-room-card-"] [data-testid="stElementContainer"]:has(.room-card-content) {
      position:absolute !important;
      top:24px !important;
      left:24px !important;
      right:24px !important;
      width:auto !important;
      max-width:none !important;
      height:132px !important;
      min-height:132px !important;
      max-height:none !important;
      margin:0 !important;
      padding:0 !important;
      overflow:visible !important;
      box-sizing:border-box !important;
    }
    div[class*="st-key-room-card-"] .room-card-content {
      display:flex !important;
      flex-direction:column !important;
      align-items:flex-start !important;
      justify-content:flex-start !important;
      gap:18px !important;
      width:100% !important;
      height:auto !important;
      min-height:0 !important;
      margin:0 !important;
      padding:2px 0 4px !important;
      overflow:visible !important;
      box-sizing:border-box !important;
    }
    div[class*="st-key-room-card-"] .room-card-name {
      width:100%;
      margin:0 !important;
      color:var(--hap-text);
      font-size:1.34rem;
      line-height:1.3;
      font-weight:800;
      letter-spacing:-.045em;
      overflow-wrap:anywhere;
      display:-webkit-box;
      -webkit-line-clamp:2;
      -webkit-box-orient:vertical;
      overflow:hidden;
    }
    div[class*="st-key-room-card-"] .room-card-meta {
      display:flex;
      align-items:center;
      flex-wrap:wrap;
      gap:17px;
      min-height:28px;
      width:100%;
      margin:0 !important;
      padding:2px 0 3px !important;
      overflow:visible !important;
      line-height:1.4 !important;
      box-sizing:border-box !important;
    }
    div[class*="st-key-room-card-"] .room-card-meta-item {
      display:inline-flex;
      align-items:baseline;
      gap:7px;
      min-height:0;
      box-sizing:border-box;
      padding:0;
      border:0 !important;
      border-radius:0;
      background:transparent !important;
      box-shadow:none !important;
    }
    div[class*="st-key-room-card-"] .room-card-meta-item + .room-card-meta-item {
      padding-left:16px;
      border-left:1px solid var(--hap-border) !important;
    }
    div[class*="st-key-room-card-"] .room-card-meta-label {
      display:inline-block;
      color:var(--hap-muted);
      font-size:.76rem;
      font-weight:650;
      line-height:1.4 !important;
      white-space:nowrap;
      overflow:visible !important;
    }
    div[class*="st-key-room-card-"] .room-card-meta-value {
      display:inline-block;
      color:var(--hap-text);
      font-size:.84rem;
      font-weight:800;
      line-height:1.4 !important;
      white-space:nowrap;
      overflow:visible !important;
    }

    /* Target the keyed button widget itself; never depend on Streamlit child order. */
    div[class*="st-key-room_open_"] {
      position:absolute !important;
      right:24px !important;
      bottom:20px !important;
      width:92px !important;
      min-width:92px !important;
      max-width:92px !important;
      height:38px !important;
      min-height:38px !important;
      margin:0 !important;
      padding:0 !important;
      overflow:visible !important;
      box-sizing:border-box !important;
      z-index:2 !important;
    }
    div[class*="st-key-room_open_"] [data-testid="stButton"] {
      display:flex !important;
      width:100% !important;
      height:100% !important;
      margin:0 !important;
      padding:0 !important;
    }
    div[class*="st-key-room_open_"] [data-testid="stBaseButton-secondary"],
    div[class*="st-key-room_open_"] [data-testid="stBaseButton-primary"] {
      display:flex !important;
      width:100% !important;
      min-width:0 !important;
      max-width:100% !important;
      min-height:38px !important;
      height:38px !important;
      margin:0 !important;
      padding:.4rem .8rem !important;
      border-radius:11px !important;
      font-size:.84rem !important;
      font-weight:750 !important;
      box-sizing:border-box !important;
    }
    div[class*="st-key-room-card-selected-"] [data-testid="stBaseButton-primary"]:disabled {
      opacity:1 !important;
      cursor:default !important;
      color:#2563EB !important;
      background:color-mix(in srgb, #2563EB 13%, var(--hap-surface)) !important;
      border:1px solid color-mix(in srgb, #2563EB 27%, var(--hap-border)) !important;
      box-shadow:var(--hap-shadow-inset) !important;
    }

    /* Expanded layout uses smaller cards but the same top-content/bottom-action structure. */
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] {
      flex:0 0 320px !important;
      width:320px !important;
      min-width:320px !important;
      max-width:320px !important;
      height:198px !important;
      min-height:198px !important;
      max-height:198px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] [data-testid="stElementContainer"]:has(.room-card-content) {
      top:18px !important;
      left:17px !important;
      right:17px !important;
      height:112px !important;
      min-height:112px !important;
      max-height:none !important;
      overflow:visible !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room_open_"] {
      right:17px !important;
      bottom:14px !important;
      width:88px !important;
      min-width:88px !important;
      max-width:88px !important;
      height:36px !important;
      min-height:36px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room_open_"] [data-testid="stBaseButton-secondary"],
    div.st-key-room-carousel-expanded div[class*="st-key-room_open_"] [data-testid="stBaseButton-primary"] {
      width:100% !important;
      min-width:0 !important;
      max-width:100% !important;
      min-height:36px !important;
      height:36px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-content {
      gap:13px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-name {
      font-size:1.18rem !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-meta {
      gap:13px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-meta-item {
      gap:6px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-meta-item + .room-card-meta-item {
      padding-left:12px !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-meta-label {
      font-size:.72rem !important;
    }
    div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] .room-card-meta-value {
      font-size:.79rem !important;
    }

    @media (max-width:680px) {
      section.main > div.block-container,
      [data-testid="stMainBlockContainer"] {
        padding-top:.85rem !important;
        padding-left:1rem !important;
        padding-right:1rem !important;
      }
      div.st-key-room-carousel div[class*="st-key-room-card-"] {
        flex-basis:min(362px, 86vw) !important;
        width:min(362px, 86vw) !important;
        min-width:min(362px, 86vw) !important;
        max-width:min(362px, 86vw) !important;
        height:224px !important;
        min-height:224px !important;
        max-height:224px !important;
      }
      div.st-key-room-carousel-expanded div[class*="st-key-room-card-"] {
        flex-basis:min(320px, 88vw) !important;
        width:min(320px, 88vw) !important;
        min-width:min(320px, 88vw) !important;
        max-width:min(320px, 88vw) !important;
        height:198px !important;
        min-height:198px !important;
        max-height:198px !important;
      }
      div[class*="st-key-room-card-"] [data-testid="stElementContainer"]:has(.room-card-content) {
        top:18px !important;
        left:16px !important;
        right:16px !important;
      }
      div[class*="st-key-room_open_"] {
        right:16px !important;
        bottom:14px !important;
      }
    }
    </style>""", unsafe_allow_html=True)
    require_login()
    global SUPABASE
    SUPABASE=get_supabase(); user_id=current_user_id()
    # 로그아웃은 별도 우측 정렬 행에 두고, 제목은 화면 전체를 기준으로 중앙 정렬한다.
    _logout_spacer, logout_col = st.columns([7, 1], vertical_alignment="center")
    with logout_col:
        if st.button('로그아웃', use_container_width=True, key="top_logout"):
            try:
                SUPABASE.auth.sign_out()
            finally:
                st.session_state.pop("_hapjugi_supabase_client", None)
                st.logout()
    st.markdown(f'<div class="app-brand-title">{APP_TITLE}</div>', unsafe_allow_html=True)

    # Apply navigation requests before creating the radio widget; Streamlit
    # does not allow changing a widget's own session-state key afterward.
    if "main_section_radio" not in st.session_state:
        st.session_state["main_section_radio"] = "내 합주실"
    pending_section = st.session_state.pop("pending_main_section", None)
    if pending_section in ["내 합주실", "방 만들기", "방 참여하기"]:
        st.session_state["main_section_radio"] = pending_section
    # 세 개 메뉴를 별도 열에 가두지 않고, 위젯 자체를 전체 화면 중앙에 둔다.
    section = st.segmented_control(
        '메뉴', ['내 합주실', '방 만들기', '방 참여하기'],
        key='main_section_radio', label_visibility='collapsed'
    )
    if section is None:
        section = '내 합주실'
    if section=='내 합주실':
        show_active_room(user_id)
    elif section=='방 만들기':
        show_room_creation(user_id)
    else:
        show_join_room()


SUPABASE: Client

if __name__ == "__main__":
    main()
