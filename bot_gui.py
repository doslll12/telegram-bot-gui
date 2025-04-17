import os
import sys
import json
import asyncio
import time
import random
import uuid
import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox as messagebox
from threading import Thread
from collections import defaultdict

from telethon import TelegramClient, events
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    ChatBannedRights,
    Channel,
    Chat
)
try:
    from telethon.tl.types import ChannelBannedRights
except ImportError:
    ChannelBannedRights = ChatBannedRights
from telethon.tl.functions.messages import (
    ImportChatInviteRequest,
    EditChatDefaultBannedRightsRequest
)
from telethon.tl.functions.channels import (
    LeaveChannelRequest,
    GetParticipantRequest,
    EditBannedRequest
)

# ─── 설정 파일 경로 및 초기화 ──────────────────────────────────

def get_config_dir():
    # Windows: %LOCALAPPDATA%\MyTelegramBot
    # macOS/Linux: ~/.config/MyTelegramBot
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~/.config")
    cfg = os.path.join(base, "MyTelegramBot")
    os.makedirs(cfg, exist_ok=True)
    return cfg

def config_path(fname: str) -> str:
    """설정 파일 전체 경로 반환"""
    return os.path.join(get_config_dir(), fname)

def ensure_config_files():
    """최초 실행 시 설정 파일이 없으면 기본값으로 생성"""
    defaults = {
        "accounts.json":      {"accounts": []},
        "alert_settings.json": {},
        "exclude_list.json":  {"join_exclude": []},
    }
    for name, data in defaults.items():
        p = config_path(name)
        if not os.path.isfile(p):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)


# ─── 설정 파일 I/O 함수 ───────────────────────────────────────

def save_alert_settings(settings):
    try:
        with open(config_path("alert_settings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("alert_settings.json 저장 오류:", e)

def load_alert_settings():
    try:
        with open(config_path("alert_settings.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def load_admin_data():
    global admin_accounts_list, admin_rooms_list, admin_function_settings, admin_enabled
    settings = load_alert_settings()
    admin_accounts_list = settings.get("admin_accounts_list", [])
    admin_rooms_list    = settings.get("admin_rooms_list", [])
    admin_function_settings = settings.get("admin_function_settings", {})
    # 기본값 보장
    default_funcs = {
        "send_message": True,
        "send_media_photo": True,
        # … 나머지도 적절히 …
    }
    for k, v in default_funcs.items():
        admin_function_settings.setdefault(k, v)
    admin_enabled = settings.get("admin_enabled", False)

def save_admin_data():
    s = load_alert_settings()
    s["admin_accounts_list"]   = admin_accounts_list
    s["admin_rooms_list"]      = admin_rooms_list
    s["admin_function_settings"]= admin_function_settings
    s["admin_enabled"]         = admin_enabled
    save_alert_settings(s)

def load_exclude_list():
    try:
        with open(config_path("exclude_list.json"), "r", encoding="utf-8") as f:
            return json.load(f).get("join_exclude", [])
    except:
        return []

def save_exclude_list(lst):
    try:
        with open(config_path("exclude_list.json"), "w", encoding="utf-8") as f:
            json.dump({"join_exclude": lst}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("exclude_list.json 저장 오류:", e)

def load_accounts():
    path = config_path("accounts.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("accounts", [])
    except Exception as e:
        print(f"accounts.json 파일 로드 오류: {e}")
        return []

def save_accounts(accs):
    path = config_path("accounts.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"accounts": accs}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"accounts.json 저장 오류: {e}")

# --------------------- 전역 ---------------------
media_groups = defaultdict(list)
media_group_timeout = 2
recent_sent_text = defaultdict(str)

is_forwarding_enabled = True    # 전체 전송 기능 ON/OFF
clients = {}                    # phone → TelegramClient
client_loops = {}               # phone → 해당 계정의 asyncio event loop
command_queues = {}             # phone → asyncio.Queue() (링크 입장/나가기)

# 메시지 수정/삭제 동기화 매핑
delete_map = {}

# GUI 전역 변수들
root = None
account_listbox = None
exclude_input = None
status_label = None
use_exclude = None

# --------------------- 방배끼기 전역 ---------------------
copy_source_chats = []
copy_exclude_senders = []
copy_enabled = False
copy_sender_mapping = {}
copy_msg_mapping = {}

copy_handler_registered = set()
expert_handler_registered = set()

# --------------------- 전문가 복사 전역 ---------------------
expert_accounts = []
expert_rooms = []
expert_names = []
expert_mode_enabled = False
expert_event_handlers = {}

# --------------------- 계정 활성화 관리 ---------------------
account_active_map = defaultdict(lambda: True)
def is_account_active(phone):
    return account_active_map.get(phone, True)

# --------------------- 전송 시간텀 관련 ---------------------
send_delay = 0.5  # 기본 전송 딜레이(초)

# --------------------- 알림 봇(멀티 계정) 관련 ---------------------
alert_bot_enabled = False  # 전체 알림 봇 기능 기본 OFF
alert_handlers = {}        # phone → 이벤트 핸들러
alert_notify_chat = None   # 알림 메시지를 보낼 채팅방 ID

# 전역: 봇 계정의 Telegram user id 저장
bot_account_ids = set()

# --------------------- 관리자 관련 ---------------------
admin_accounts_list = []
admin_rooms_list = []
admin_function_settings = {}
admin_enabled = False  # 프로그램 시작 시 관리자 기능 기본 OFF

# --------------------- 설정 파일 관련 함수 ---------------------

def load_admin_data():
    global admin_accounts_list, admin_rooms_list, admin_function_settings, admin_enabled
    settings = load_alert_settings()
    admin_accounts_list = settings.get("admin_accounts_list", [])
    admin_rooms_list = settings.get("admin_rooms_list", [])
    admin_function_settings = settings.get("admin_function_settings", {})
    default_funcs = {
       "send_message": True,
       "send_media_photo": True,
       "send_media_video_file": True,
       "send_media_video_message": True,
       "send_media_music": True,
       "send_media_voice": True,
       "send_media_file": True,
       "send_media_sticker_gif": True,
       "send_media_link": True,
       "send_media_poll": True,
       "add_participant": True,
       "pin_message": True,
       "change_group_info": True
    }
    for k, default_val in default_funcs.items():
        if k not in admin_function_settings:
            admin_function_settings[k] = default_val
    admin_enabled = settings.get("admin_enabled", False)

def save_admin_data():
    settings = load_alert_settings()
    settings["admin_accounts_list"] = admin_accounts_list
    settings["admin_rooms_list"] = admin_rooms_list
    settings["admin_function_settings"] = admin_function_settings
    settings["admin_enabled"] = admin_enabled
    save_alert_settings(settings)

# --------------------- 정규화 함수 (chat_id 비교 용) ---------------------
def normalize_chat_id(chat_id):
    s = str(chat_id)
    if s.startswith("-100"):
        return chat_id
    if s.startswith("-"):
        return int("-100" + s[1:])
    return chat_id

# --------------------- 로그인 처리 ---------------------
async def login_accounts():
    accounts = load_accounts()
    for idx, acc in enumerate(accounts):
        phone = acc["phone"]
        session_name = acc.get("session_name", f"session_{phone}")
        client = TelegramClient(session_name, acc["api_id"], acc["api_hash"])
        try:
            await client.connect()
            if await client.is_user_authorized():
                print(f"[{idx+1}] {phone} 이미 로그인되어 있음 ✅")
                continue
            print(f"[{idx+1}] {phone} 인증코드 입력:")
            try:
                await client.send_code_request(phone)
                code = input(f"→ 인증코드 입력: ").strip()
                try:
                    await client.sign_in(phone, code)
                except Exception as e:
                    await client.sign_in(password=acc["password"])
                print(f"{phone} 로그인 성공 및 세션 저장 ✅")
            except Exception as e:
                print(f"{phone} 로그인 실패: {e}")
        finally:
            await client.disconnect()

def start_login_process():
    def _do_login():
        accounts = load_accounts()
        if accounts:
            asyncio.run(login_accounts())
            for idx, acc in enumerate(accounts):
                Thread(target=lambda a=acc, i=idx: asyncio.run(account_task(a, i))).start()
        else:
            print("등록된 계정이 없습니다. (accounts.json)")
        Thread(target=run_copy_monitor, daemon=True).start()
        update_alert_handlers()
        def after_login_refresh():
            try:
                refresh_watch_accounts_list_global()
            except:
                pass
        if root:
            root.after(1500, after_login_refresh)
    Thread(target=_do_login, daemon=True).start()

# --------------------- 메인방 → 서브방 전송 ---------------------
def forward_to_subrooms(client, account, message, target_rooms=None):
    async def _forward():
        phone = account["phone"]
        rooms = target_rooms if target_rooms is not None else account.get("subroom_ids", [])
        if message.text and not message.media:
            for r in rooms:
                try:
                    ent = await client.get_input_entity(r)
                    msg_sent = await client.send_message(
                        ent, message.raw_text,
                        formatting_entities=message.entities,
                        link_preview=True
                    )
                    delete_map.setdefault((phone, message.id), []).append((r, msg_sent.id))
                except Exception as e:
                    print(f"{phone} 서브방 텍스트 오류: {e}")
                await asyncio.sleep(send_delay)
        elif message.media:
            for r in rooms:
                try:
                    ent = await client.get_input_entity(r)
                    msg_sent = await client.send_file(
                        ent, file=message.media,
                        caption=message.raw_text,
                        formatting_entities=message.entities if message.raw_text else None
                    )
                    if isinstance(msg_sent, list):
                        for s_m in msg_sent:
                            delete_map.setdefault((phone, message.id), []).append((r, s_m.id))
                    else:
                        delete_map.setdefault((phone, message.id), []).append((r, msg_sent.id))
                except Exception as e:
                    print(f"{phone} 서브방 미디어 오류: {e}")
                await asyncio.sleep(send_delay)
    return _forward

def forward_to_subrooms_expert(client, account, key, message, target_rooms=None):
    async def _forward():
        phone = account["phone"]
        rooms = target_rooms if target_rooms is not None else account.get("subroom_ids", [])
        if message.text and not message.media:
            for r in rooms:
                try:
                    ent = await client.get_input_entity(r)
                    msg_sent = await client.send_message(
                        ent, message.raw_text,
                        formatting_entities=message.entities
                    )
                    delete_map.setdefault(key, []).append((r, msg_sent.id))
                except Exception as e:
                    print(f"{phone} 전문가 서브방 텍스트 오류: {e}")
                await asyncio.sleep(send_delay)
        elif message.media:
            for r in rooms:
                try:
                    ent = await client.get_input_entity(r)
                    msg_sent = await client.send_file(
                        ent, file=message.media,
                        caption=message.raw_text,
                        formatting_entities=message.entities if message.raw_text else None
                    )
                    if isinstance(msg_sent, list):
                        for s_m in msg_sent:
                            delete_map.setdefault(key, []).append((r, s_m.id))
                    else:
                        delete_map.setdefault(key, []).append((r, msg_sent.id))
                except Exception as e:
                    print(f"{phone} 전문가 서브방 미디어 오류: {e}")
                await asyncio.sleep(send_delay)
    return _forward

async def handle_new_message(event, client, subroom_ids, account):
    me = await client.get_me()
    phone = me.phone.lstrip("+")
    if not is_account_active(phone):
        return
    if event.sender_id != me.id and not ("http://" in event.raw_text or "https://" in event.raw_text):
        return
    if not is_forwarding_enabled:
        return

    grouped_id = getattr(event.message, "grouped_id", None)
    if grouped_id:
        media_groups[grouped_id].append(event.message)
        await asyncio.sleep(media_group_timeout)
        if media_groups[grouped_id]:
            await flush_media_group(client, grouped_id, media_groups[grouped_id], subroom_ids, phone)
        return

    if event.text and not event.media:
        for r in subroom_ids:
            try:
                ent = await client.get_input_entity(r)
                msg_sent = await client.send_message(
                    ent, event.raw_text,
                    formatting_entities=event.message.entities,
                    link_preview=True
                )
                delete_map.setdefault((phone, event.id), []).append((r, msg_sent.id))
            except Exception as e:
                print(f"{phone} 서브방 텍스트 오류: {e}")
            await asyncio.sleep(send_delay)
        recent_sent_text[phone] = event.raw_text
    elif event.media and isinstance(event.media, (MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage)):
        caption = event.raw_text
        if caption == recent_sent_text.get(phone):
            caption = None
        for r in subroom_ids:
            try:
                ent = await client.get_input_entity(r)
                msg_sent = await client.send_file(
                    ent, file=event.media,
                    caption=caption,
                    formatting_entities=event.message.entities if caption else None
                )
                delete_map.setdefault((phone, event.id), []).append((r, msg_sent.id) if not isinstance(msg_sent, list) else [(r, s.id) for s in msg_sent])
            except Exception as e:
                print(f"{phone} 서브방 미디어 오류: {e}")
            await asyncio.sleep(send_delay)
        recent_sent_text[phone] = ""

async def flush_media_group(client, group_id, messages, subroom_ids, phone):
    if not messages:
        return
    first_msg = messages[0]
    if not subroom_ids:
        return
    for r in subroom_ids:
        try:
            ent = await client.get_input_entity(r)
            sent = await client.send_file(
                ent, files=[m.media for m in messages],
                caption=first_msg.raw_text,
                formatting_entities=first_msg.entities if first_msg.raw_text else None
            )
            for m, s_m in zip(messages, sent):
                delete_map.setdefault((phone, m.id), []).append((r, s_m.id))
        except Exception as e:
            print(f"{phone} 미디어그룹 전송 오류: {e}")
        await asyncio.sleep(send_delay)
    media_groups[group_id].clear()

async def handle_message_edit(event, client, subroom_ids, account):
    me = await client.get_me()
    phone = me.phone.lstrip("+")
    if not is_account_active(phone):
        return
    if event.sender_id != me.id:
        return
    recent_main = [m async for m in client.iter_messages(account["main_chat_id"], from_user="me", limit=50)][:20]
    수정_idx = -1
    for i, m in enumerate(recent_main):
        if m.id == event.id:
            수정_idx = i
            break
    if 수정_idx == -1:
        return
    is_sup_media = isinstance(event.media, (MessageMediaPhoto, MessageMediaDocument))
    is_media_different = is_sup_media and bool(event.media)
    for r in subroom_ids:
        sub_ent = await client.get_input_entity(r)
        sub_msgs = [x async for x in client.iter_messages(sub_ent, from_user="me", limit=50)][:20]
        if len(sub_msgs) <= 수정_idx:
            continue
        target_msg = sub_msgs[수정_idx]
        is_text_diff = (target_msg.raw_text != event.raw_text)
        is_ent_diff  = (target_msg.entities != event.message.entities)
        if is_text_diff or is_ent_diff or is_media_different:
            try:
                await client.edit_message(
                    sub_ent, target_msg.id,
                    event.raw_text,
                    formatting_entities=event.message.entities,
                    file=event.media if is_sup_media else None
                )
            except Exception as e:
                print(f"{phone} 서브방 수정 오류: {e}")
    await asyncio.sleep(send_delay)

async def handle_deleted_event(event, phone):
    phone = phone.lstrip("+")
    if not is_account_active(phone):
        return
    for del_id in event.deleted_ids:
        key = (phone, del_id)
        if key in delete_map:
            for (sub_cid, sub_mid) in delete_map[key]:
                try:
                    await clients[phone].delete_messages(sub_cid, sub_mid)
                except Exception as e:
                    print(f"{phone} 서브 메시지 삭제 오류: {e}")
            delete_map.pop(key, None)

# --------------------- 채팅방 입장/나가기 처리 ---------------------
async def join_chat_task(client, link, phone):
    print(f"[{phone}] 입장 시도...")
    try:
        entity = await client.get_input_entity(link)
        try:
            await client(GetParticipantRequest(entity, 'me'))
        except:
            pass
    except:
        try:
            hash_part = link.split('/')[-1].replace('+','')
            await client(ImportChatInviteRequest(hash_part))
        except Exception as e:
            print(f"[{phone}] 입장 오류: {e}")

async def leave_chat_task(client, link, phone):
    print(f"[{phone}] 나가기 시도...")
    try:
        entity = await client.get_input_entity(link)
        if not hasattr(entity, 'id') or not hasattr(entity, 'access_hash'):
            print(f"[{phone}] 나가기 오류: 일반그룹(chat)은 나가기 불가")
            return
        await client(LeaveChannelRequest(entity))
    except Exception as e:
        print(f"[{phone}] 나가기 오류: {e}")

async def handle_commands(phone, client):
    q = command_queues[phone]
    while True:
        cmd = await q.get()
        if cmd["type"] == "join":
            await join_chat_task(client, cmd["link"], phone)
        elif cmd["type"] == "leave":
            await leave_chat_task(client, cmd["link"], phone)

# --------------------- 계정 작업 ---------------------
async def account_task(account, idx=0):
    phone = account["phone"]
    session_name = account.get("session_name", f"session_{phone}")
    client = TelegramClient(session_name, account["api_id"], account["api_hash"])
    while True:
        try:
            await client.connect()
            if not await client.is_user_authorized():
                print(f"{phone} 로그인 안됨 → 메시지감지X")
                return
            me = await client.get_me()
            bot_account_ids.add(me.id)
            if "main_chat_id" in account:
                @client.on(events.NewMessage(chats=[account["main_chat_id"]]))
                async def new_msg_handler(ev):
                    await handle_new_message(ev, client, account.get("subroom_ids", []), account)
                @client.on(events.MessageEdited(chats=[account["main_chat_id"]]))
                async def edit_msg_handler(ev):
                    await handle_message_edit(ev, client, account.get("subroom_ids", []), account)
                @client.on(events.MessageDeleted(chats=[account["main_chat_id"]]))
                async def delete_msg_handler(ev):
                    await handle_deleted_event(ev, phone)
            clients[phone] = client
            client_loops[phone] = asyncio.get_running_loop()
            command_queues[phone] = asyncio.Queue()
            await asyncio.gather(
                handle_commands(phone, client),
                client.run_until_disconnected()
            )
        except Exception as e:
            print(f"[{phone}] 연결 오류: {e}. 재연결 시도 중...")
            await asyncio.sleep(5)
        finally:
            try:
                await client.disconnect()
            except:
                pass

def start_account_task(acc, idx):
    asyncio.run(account_task(acc, idx))

# ------------------ 알림 봇(멀티 계정) 핸들러 관리 ---------------------
def update_alert_handlers():
    global alert_handlers
    current_acc_list = load_accounts()
    for phone in list(alert_handlers.keys()):
        acc = next((a for a in current_acc_list if a["phone"] == phone), None)
        if not acc or not acc.get("alert_monitor", False) or phone not in clients:
            remove_alert_handler_multi(phone)
            alert_handlers.pop(phone, None)
    for acc in current_acc_list:
        phone = acc["phone"]
        if acc.get("alert_monitor", False) and phone in clients and phone not in alert_handlers:
            h = make_alert_handler(phone)
            clients[phone].add_event_handler(h, events.NewMessage)
            alert_handlers[phone] = h
            print(f"[알림 봇] {phone} 이벤트 핸들러 등록 완료")

def remove_alert_handler_multi(phone):
    if phone in alert_handlers:
        client = clients.get(phone)
        if client:
            client.remove_event_handler(alert_handlers[phone], events.NewMessage)
        print(f"[알림 봇] {phone} 계정 이벤트 핸들러 제거")

def make_alert_handler(phone):
    async def handler(event):
        try:
            acc = get_account_by_phone(phone)
            if not acc:
                return
            if not acc.get("alert_monitor", False):
                return
            rooms = acc.get("alert_rooms", [])
            if rooms and event.chat_id not in rooms:
                return
            client = clients.get(phone)
            if not client:
                return
            if event.sender_id in bot_account_ids:
                return
            me = await client.get_me()
            if event.sender_id == me.id:
                return
            async def find_room_name():
                async for d in client.iter_dialogs():
                    if d.id == event.chat_id:
                        return d.name
                return None
            room_name = await find_room_name() or "Unknown"
            sender = await event.get_sender()
            sender_name = ((sender.first_name or "") + " " + (sender.last_name or "")).strip() or sender.username or "Unknown"
            print(f"{room_name} : 설정 완료")
            if alert_notify_chat:
                await client.send_message(alert_notify_chat, 
                    f"방이름: {room_name} / 방아이디: {event.chat_id} / 보낸이: {sender_name} / 내용: {event.raw_text}")
        except Exception as e:
            print(f"[ERROR] make_alert_handler 예외 발생: {e}")
    return handler

# --------------------- 관리자 기능 적용 (관리자 관리 탭 '적용' 버튼) ---------------------
def apply_admin_functions():
    # 1) 체크박스 상태 저장
    for k, var in admin_var_map.items():
        admin_function_settings[k] = var.get()
    save_admin_data()

    print("[관리자 기능] 설정 적용 중...")
    for k, val in admin_function_settings.items():
        print(f"  - {k} = {val}")

    # 2) room id 리스트 정수형으로 변환
    try:
        room_ids = [int(r) for r in admin_rooms_list]
    except:
        print("admin_rooms_list 에 숫자형 ID만 있어야 합니다.")
        return

    # 3) 권한 객체 준비
    def is_banned(f):
        return not admin_function_settings.get(f, True)

    # ChatBannedRights 의 세부 플래그들 (send_* = 금지하려면 True)
    new_rights_chat = ChatBannedRights(
        until_date=None,
        send_plain       = is_banned("send_message"),               # 텍스트 메시지
        send_photos      = is_banned("send_media_photo"),           # 사진
        send_videos      = is_banned("send_media_video_file"),      # 영상 파일
        send_roundvideos = is_banned("send_media_video_message"),   # 영상 메시지(비디오 노트)
        send_audios      = is_banned("send_media_music"),           # 음악 파일
        send_voices      = is_banned("send_media_voice"),           # 음성 메시지
        send_docs        = is_banned("send_media_file"),            # 일반 파일
        send_gifs        = is_banned("send_media_sticker_gif"),     # 스티커/GIF
        embed_links      = is_banned("send_media_link"),            # 링크
        send_polls       = is_banned("send_media_poll"),            # 설문
        invite_users     = is_banned("add_participant"),            # 참가자 추가
        pin_messages     = is_banned("pin_message"),                # 메시지 고정
        change_info      = is_banned("change_group_info")           # 그룹 정보 변경
    )

    # ChannelBannedRights 에도 동일하게 맵핑 (슈퍼그룹/채널용)
    new_rights_channel = ChannelBannedRights(
        until_date=None,
        send_messages    = is_banned("send_message"),
        send_photos      = is_banned("send_media_photo"),
        send_videos      = is_banned("send_media_video_file"),
        send_roundvideos = is_banned("send_media_video_message"),
        send_audios      = is_banned("send_media_music"),
        send_voices      = is_banned("send_media_voice"),
        send_docs        = is_banned("send_media_file"),
        send_gifs        = is_banned("send_media_sticker_gif"),
        embed_links      = is_banned("send_media_link"),
        send_polls       = is_banned("send_media_poll"),
        invite_users     = is_banned("add_participant"),
        pin_messages     = is_banned("pin_message"),
        change_info      = is_banned("change_group_info")
    )

    # 4) 사용할 관리자 계정 선택
    if not admin_accounts_list:
        print("관리자 계정이 없습니다. 권한 적용 불가")
        return

    chosen = next((p for p in admin_accounts_list if p in clients), None)
    if not chosen:
        print("연결된 관리자 계정이 없어 권한 적용 불가")
        return

    client = clients[chosen]
    loop   = client_loops.get(chosen)
    if not loop:
        print("이 관리자 계정에 대한 event loop가 없습니다.")
        return

    # 5) 캐시에 있는 peer 엔티티들 미리 불러오기
    peer_map = {}
    async def cache_peers():
        async for d in client.iter_dialogs():
            if d.id in room_ids:
                peer_map[d.id] = d.entity

    # 블로킹 없이 동기적으로 대기
    asyncio.run_coroutine_threadsafe(cache_peers(), loop).result(timeout=10)

    # 6) 실제 권한 적용
    async def apply_room(rid):
        peer = peer_map.get(rid)
        if not peer:
            print(f"{rid} 엔티티가 캐시에 없습니다. 스킵합니다.")
            return

        # 일반그룹 권한
        try:
            await client(EditChatDefaultBannedRightsRequest(
                peer=peer,
                banned_rights=new_rights_chat
            ))
            print(f"{rid} → 일반그룹 권한 적용 완료")
        except Exception:
            pass

        # 슈퍼그룹/채널 권한
        try:
            await client(EditBannedRequest(
                channel=peer,
                participant="me",
                banned_rights=new_rights_channel
            ))
            print(f"{rid} → 채널 권한 적용 완료")
        except Exception as e:
            print(f"{rid} → 채널 권한 적용 실패: {e}")

    # 7) 각 방에 코루틴 스케줄
    for rid in room_ids:
        asyncio.run_coroutine_threadsafe(apply_room(rid), loop)

# --------------------- 관리자 관리 탭 ---------------------
def build_admin_tab(tab):
    global admin_enabled, admin_accounts_list, admin_rooms_list, admin_function_settings, admin_var_map
    container = ttk.Frame(tab)
    container.pack(fill="both", expand=True, padx=10, pady=10)
    frame_toggle = ttk.Frame(container)
    frame_toggle.pack(fill="x", pady=5)
    admin_enabled_var = tk.BooleanVar(value=admin_enabled)
    def on_admin_enabled_toggle():
        global admin_enabled
        admin_enabled = admin_enabled_var.get()
        save_admin_data()
        state_str = "ON" if admin_enabled else "OFF"
        print(f"[관리자 기능] -> {state_str}")
    chk_admin_enabled = ttk.Checkbutton(frame_toggle, text="관리자 기능 활성화",
                                        variable=admin_enabled_var,
                                        command=on_admin_enabled_toggle)
    chk_admin_enabled.pack(side="left")
    frame_accounts = ttk.Frame(container)
    frame_accounts.pack(fill="x", pady=5)
    frame_all_accounts = ttk.Frame(frame_accounts)
    frame_all_accounts.pack(side="left", fill="both", expand=True, padx=5)
    ttk.Label(frame_all_accounts, text="모든 계정 목록").pack(anchor="w")
    account_search_var_admin = tk.StringVar()
    entry_search_admin = ttk.Entry(frame_all_accounts, textvariable=account_search_var_admin)
    entry_search_admin.pack(fill="x")
    all_accounts_lb = tk.Listbox(frame_all_accounts, height=8)
    all_accounts_lb.pack(fill="both", expand=True)
    def refresh_all_accounts_admin_list():
        all_accounts_lb.delete(0, tk.END)
        search_term = account_search_var_admin.get().strip()
        for acc in load_accounts():
            phone = acc["phone"]
            if search_term in phone:
                all_accounts_lb.insert(tk.END, phone)
    ttk.Button(frame_all_accounts, text="찾기", command=refresh_all_accounts_admin_list).pack(pady=2)
    frame_admin_accounts = ttk.Frame(frame_accounts)
    frame_admin_accounts.pack(side="left", fill="both", expand=True, padx=5)
    ttk.Label(frame_admin_accounts, text="관리자 계정 목록").pack(anchor="w")
    admin_accounts_lb = tk.Listbox(frame_admin_accounts, height=8)
    admin_accounts_lb.pack(fill="both", expand=True)
    def add_admin_account():
        try:
            selection = all_accounts_lb.get(all_accounts_lb.curselection())
        except:
            return
        if selection not in admin_accounts_list:
            admin_accounts_list.append(selection)
            admin_accounts_lb.insert(tk.END, selection)
            save_admin_data()
    def remove_admin_account():
        try:
            idx = admin_accounts_lb.curselection()[0]
            selection = admin_accounts_lb.get(idx)
        except:
            return
        if selection in admin_accounts_list:
            admin_accounts_list.remove(selection)
            admin_accounts_lb.delete(idx)
            save_admin_data()
    btn_acc_frame = ttk.Frame(frame_admin_accounts)
    btn_acc_frame.pack(pady=2)
    ttk.Button(btn_acc_frame, text="➕ 추가", command=add_admin_account).pack(side="left", padx=2)
    ttk.Button(btn_acc_frame, text="🗑 삭제", command=remove_admin_account).pack(side="left", padx=2)
    frame_admin_chats = ttk.Frame(container)
    frame_admin_chats.pack(fill="both", expand=True, pady=5)
    ttk.Label(frame_admin_chats, text="관리자 계정의 대화방 목록").pack(anchor="w")
    admin_chats_lb = tk.Listbox(frame_admin_chats, height=6)
    admin_chats_lb.pack(fill="both", expand=True)
    def refresh_admin_chats():
        admin_chats_lb.delete(0, tk.END)
        try:
            idx = admin_accounts_lb.curselection()[0]
            selected_phone = admin_accounts_lb.get(idx)
        except:
            admin_chats_lb.insert(tk.END, "관리자 계정을 먼저 선택하세요.")
            return
        if selected_phone not in clients:
            admin_chats_lb.insert(tk.END, "이 계정의 클라이언트가 준비되지 않음(로그인 필요)")
            return
        c = clients[selected_phone]
        loop = client_loops.get(selected_phone)
        if not loop:
            admin_chats_lb.insert(tk.END, "이 계정의 event loop가 없음")
            return
        async def fetch_dialogs():
            results = []
            async for d in c.iter_dialogs():
                results.append((d.name, d.id))
            return results
        future = asyncio.run_coroutine_threadsafe(fetch_dialogs(), loop)
        try:
            dialogs = future.result(timeout=10)
            if dialogs:
                for nm, cid in dialogs:
                    admin_chats_lb.insert(tk.END, f"{nm} (ID={cid})")
            else:
                admin_chats_lb.insert(tk.END, "대화방 없음")
        except Exception as e:
            admin_chats_lb.insert(tk.END, f"불러오기 실패: {e}")
    ttk.Button(frame_admin_chats, text="새로고침", command=refresh_admin_chats).pack(pady=2)
    async def get_chat_name_by_id_for_admin(chat_id):
        for admin_phone in admin_accounts_list:
            if admin_phone in clients:
                c = clients[admin_phone]
                try:
                    ent = await c.get_entity(chat_id)
                    return ent.title if hasattr(ent, 'title') else (ent.first_name or "Unknown")
                except:
                    pass
        return "Unknown"
    def refresh_admin_rooms_listbox():
        admin_rooms_lb.delete(0, tk.END)
        loop = None
        for admin_phone in admin_accounts_list:
            if admin_phone in client_loops:
                loop = client_loops[admin_phone]
                break
        if not loop:
            for rid in admin_rooms_list:
                admin_rooms_lb.insert(tk.END, rid)
            return
        for rid in admin_rooms_list:
            def fetch_name(id_):
                return asyncio.run_coroutine_threadsafe(get_chat_name_by_id_for_admin(id_), loop).result(5)
            try:
                chat_name = fetch_name(rid)
                admin_rooms_lb.insert(tk.END, f"{chat_name} (방아이디: {rid})")
            except:
                admin_rooms_lb.insert(tk.END, rid)
    frame_admin_rooms = ttk.Frame(container)
    frame_admin_rooms.pack(fill="both", expand=True, pady=5)
    ttk.Label(frame_admin_rooms, text="관리자 방 목록").pack(anchor="w")
    admin_rooms_lb = tk.Listbox(frame_admin_rooms, height=6)
    admin_rooms_lb.pack(fill="both", expand=True)
    def add_admin_room_from_selected_chat():
        try:
            sel = admin_chats_lb.get(admin_chats_lb.curselection())
        except:
            messagebox.showinfo("알림", "대화방 목록에서 선택하세요.")
            return
        import re
        match = re.search(r'ID=([\-0-9]+)', sel)
        if match:
            chat_id = match.group(1)
            if chat_id not in admin_rooms_list:
                admin_rooms_list.append(chat_id)
                refresh_admin_rooms_listbox()
                save_admin_data()
                print("방이름 : 설정 완료")
        else:
            messagebox.showerror("오류", "ID 파싱 실패")
    ttk.Button(frame_admin_rooms, text="대화방 → 추가", command=add_admin_room_from_selected_chat).pack(pady=3)
    frame_room_entry = ttk.Frame(frame_admin_rooms)
    frame_room_entry.pack(fill="x")
    room_entry_var = tk.StringVar()
    room_entry = ttk.Entry(frame_room_entry, textvariable=room_entry_var)
    room_entry.pack(side="left", fill="x", expand=True, padx=5)
    def add_admin_room_by_input():
        val = room_entry_var.get().strip()
        if val and val not in admin_rooms_list:
            admin_rooms_list.append(val)
            refresh_admin_rooms_listbox()
            room_entry_var.set("")
            save_admin_data()
    ttk.Button(frame_room_entry, text="➕ 추가(직접입력)", command=add_admin_room_by_input).pack(side="left", padx=2)
    def remove_admin_room():
        try:
            idx = admin_rooms_lb.curselection()[0]
            selection = admin_rooms_lb.get(idx)
        except:
            return
        if selection in admin_rooms_list:
            admin_rooms_list.remove(selection)
            admin_rooms_lb.delete(idx)
            save_admin_data()
    ttk.Button(frame_admin_rooms, text="🗑 삭제", command=remove_admin_room).pack(pady=2)
    frame_admin_funcs = ttk.Frame(container)
    frame_admin_funcs.pack(fill="both", expand=True, pady=5)
    ttk.Label(frame_admin_funcs, text="관리자 기능 설정").pack(anchor="w")
    func_left = ttk.Frame(frame_admin_funcs)
    func_left.pack(side="left", fill="both", expand=True)
    func_right = ttk.Frame(frame_admin_funcs)
    func_right.pack(side="left", fill="both", expand=True)
    global admin_var_map
    admin_var_map = {}
    admin_funcs = {
       "send_message": "메시지 보내기",
       "send_media_photo": "사진 보내기",
       "send_media_video_file": "영상파일 보내기",
       "send_media_video_message": "영상 메시지 보내기",
       "send_media_music": "음악 보내기",
       "send_media_voice": "음성 메시지 보내기",
       "send_media_file": "파일 보내기",
       "send_media_sticker_gif": "스티커/GIF 보내기",
       "send_media_link": "링크 보내기",
       "send_media_poll": "설문 보내기",
       "add_participant": "참가자 추가",
       "pin_message": "메시지 고정",
       "change_group_info": "그룹 정보 변경"
    }
    keys = list(admin_funcs.keys())
    for i, key in enumerate(keys):
        var = tk.BooleanVar(value=admin_function_settings.get(key, True))
        admin_var_map[key] = var
        target_frame = func_left if i % 2 == 0 else func_right
        chk = ttk.Checkbutton(target_frame, text=admin_funcs[key], variable=var)
        chk.pack(anchor="w", padx=5, pady=2)
    ttk.Button(frame_admin_funcs, text="적용", command=apply_admin_functions).pack(pady=5)
    for phone in admin_accounts_list:
        if phone not in admin_accounts_lb.get(0, tk.END):
            admin_accounts_lb.insert(tk.END, phone)
    refresh_admin_rooms_listbox()

# --------------------- (중요) 전역 참조 ---------------------
refresh_watch_accounts_list_global = None

# --------------------- 메인 실행 ---------------------
def run_main():
    Thread(target=start_gui).start()

def start_gui():
    global root, alert_notify_chat, admin_enabled
    load_admin_data()
    # 프로그램 시작 시 관리자 기능 기본 OFF로 초기화
    admin_enabled = False
    save_admin_data()
    settings = load_alert_settings()
    alert_notify_chat = settings.get("alert_notify_chat", None)
    root = tk.Tk()
    root.title("텔레그램 자동화 프로그램")
    root.geometry("900x900")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    tab_main = ttk.Frame(notebook)
    tab_copy = ttk.Frame(notebook)
    tab_expert = ttk.Frame(notebook)
    tab_account = ttk.Frame(notebook)
    tab_alert = ttk.Frame(notebook)
    tab_admin = ttk.Frame(notebook)  # 관리자 관리 탭
    notebook.add(tab_main, text="메인")
    notebook.add(tab_copy, text="방배끼기")
    notebook.add(tab_expert, text="전문가 셋팅")
    notebook.add(tab_account, text="계정관리")
    notebook.add(tab_alert, text="방 알림 봇")
    notebook.add(tab_admin, text="관리자 관리")
    build_main_tab(tab_main)
    build_copy_tab(tab_copy)
    build_expert_tab(tab_expert)
    build_account_management_tab(tab_account)
    build_alert_bot_tab_multi(tab_alert)
    build_admin_tab(tab_admin)
    root.mainloop()

def build_main_tab(parent):
    container = ttk.Frame(parent)
    container.pack(fill="both", expand=True)
    canvas = tk.Canvas(container)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    scroll_frame = ttk.Frame(canvas)
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0,0), window=scroll_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    ttk.Button(scroll_frame, text="로그인 시작", command=start_login_process).pack(pady=5)
    def stop_forwarding():
        global is_forwarding_enabled
        is_forwarding_enabled = False
        status_label.config(text="⛔ 전송 정지됨")
    def start_forwarding():
        global is_forwarding_enabled
        is_forwarding_enabled = True
        status_label.config(text="✅ 전송 가능")
    top_line = ttk.Frame(scroll_frame)
    top_line.pack(pady=5, fill="x")
    global status_label
    status_label = ttk.Label(top_line, text="✅ 전송 가능", width=20)
    status_label.pack(side="left", padx=5)
    ttk.Button(top_line, text="정지", width=10, command=stop_forwarding).pack(side="left", padx=2)
    ttk.Button(top_line, text="정지해지", width=10, command=start_forwarding).pack(side="left", padx=2)
    delay_frame = ttk.Frame(scroll_frame)
    delay_frame.pack(pady=5, fill="x")
    ttk.Label(delay_frame, text="전송 시간텀 (초): ").pack(side="left")
    delay_var = tk.StringVar(value=str(send_delay))
    delay_entry = ttk.Entry(delay_frame, textvariable=delay_var, width=10)
    delay_entry.pack(side="left")
    def apply_delay():
        global send_delay
        try:
            new_delay = float(delay_var.get())
            send_delay = new_delay
            print(f"전송 시간텀 적용: {send_delay} 초")
        except:
            print("시간텀 설정 오류")
    ttk.Button(delay_frame, text="적용", command=apply_delay).pack(side="left", padx=5)
    ttk.Label(scroll_frame, text="텔레그램 초대 링크 입력").pack(pady=(10,0))
    link_var = tk.StringVar()
    ttk.Entry(scroll_frame, textvariable=link_var, width=60).pack(pady=3)
    action_frame = ttk.Frame(scroll_frame)
    action_frame.pack(pady=5)
    global use_exclude
    use_exclude = tk.BooleanVar(value=False)
    ttk.Checkbutton(scroll_frame, text="입장 제외자 사용 (ON/OFF)", variable=use_exclude).pack(pady=3, anchor="w")
    def join_chat_all_accounts(link):
        ex = set(load_exclude_list())
        for phone in command_queues:
            if use_exclude.get() and phone in ex:
                print(f"[{phone}] 입장 제외 → 건너뜀")
                continue
            command_queues[phone].put_nowait({"type": "join", "link": link})
    def leave_chat_all_accounts(link):
        for phone in command_queues:
            command_queues[phone].put_nowait({"type": "leave", "link": link})
    ttk.Button(action_frame, text="전체 계정 입장", command=lambda: join_chat_all_accounts(link_var.get())).pack(side="left", padx=5)
    ttk.Button(action_frame, text="전체 계정 나가기", command=lambda: leave_chat_all_accounts(link_var.get())).pack(side="left", padx=5)
    ttk.Label(scroll_frame, text="제외할 전화번호 추가/삭제").pack(pady=(15,0))
    global exclude_input
    exclude_input = ttk.Entry(scroll_frame, width=40)
    exclude_input.pack()
    lb = tk.Listbox(scroll_frame, height=5)
    lb.pack(pady=3)
    def refresh_exclude_listbox():
        lb.delete(0, tk.END)
        for ph in load_exclude_list():
            lb.insert(tk.END, ph)
    def add_exclude():
        nm = exclude_input.get().strip()
        if nm:
            ex_list = load_exclude_list()
            if nm not in ex_list:
                ex_list.append(nm)
                save_exclude_list(ex_list)
                refresh_exclude_listbox()
    def remove_exclude():
        try:
            sel = lb.get(lb.curselection())
            ex_list = load_exclude_list()
            if sel in ex_list:
                ex_list.remove(sel)
                save_exclude_list(ex_list)
                refresh_exclude_listbox()
        except:
            pass
    ttk.Button(scroll_frame, text="➕ 추가", command=add_exclude).pack(pady=2)
    ttk.Button(scroll_frame, text="🗑 삭제", command=remove_exclude).pack()
    refresh_exclude_listbox()
    ttk.Label(scroll_frame, text="로그인된 계정 목록").pack(pady=(15,0))
    global account_listbox
    account_listbox = tk.Listbox(scroll_frame, height=5)
    account_listbox.pack(pady=3)
    def on_account_select(event):
        try:
            idx = account_listbox.curselection()[0]
            sel_phone = account_listbox.get(idx)
            exclude_input.delete(0, tk.END)
            exclude_input.insert(0, sel_phone)
        except:
            pass
    account_listbox.bind("<<ListboxSelect>>", on_account_select)
    def update_account_list():
        account_listbox.delete(0, tk.END)
        for acc in load_accounts():
            account_listbox.insert(tk.END, acc["phone"])
        print("계정 목록 갱신 완료 (accounts.json)")
    update_account_list()
    ttk.Label(scroll_frame, text="실시간 로그").pack(pady=(15,0))
    log_box = tk.Text(scroll_frame, height=15, state="disabled", bg="black", fg="white")
    log_box.pack(fill="x", padx=5, pady=5)
    class DualWriter:
        def __init__(self, gui_log_func):
            self.gui_log_func = gui_log_func
            self.console = sys.__stdout__
        def write(self, text):
            if text.strip():
                self.console.write(text + "\n")
                self.console.flush()
                self.gui_log_func(text)
        def flush(self):
            self.console.flush()
    def write_log(text):
        log_box.configure(state="normal")
        log_box.insert(tk.END, text + "\n")
        log_box.see(tk.END)
        log_box.configure(state="disabled")
    sys.stdout = DualWriter(write_log)
    sys.stderr = DualWriter(write_log)

def build_copy_tab(tab):
    tk.Label(tab, text="복사 소스 채팅 ID 목록").pack(pady=(5, 0))
    source_input = ttk.Entry(tab, width=40)
    source_input.pack()
    source_listbox = tk.Listbox(tab, height=5)
    source_listbox.pack(pady=5)
    def refresh_source_list():
        source_listbox.delete(0, tk.END)
        for cid in copy_source_chats:
            source_listbox.insert(tk.END, str(cid))
    def add_source():
        val = source_input.get().strip()
        if val:
            try:
                cid_int = int(val)
                if cid_int not in copy_source_chats:
                    copy_source_chats.append(cid_int)
                    refresh_source_list()
            except:
                pass
    def remove_source():
        try:
            sel = source_listbox.get(source_listbox.curselection())
            cid_int = int(sel)
            copy_source_chats.remove(cid_int)
            refresh_source_list()
        except:
            pass
    ttk.Button(tab, text="➕ 추가", command=add_source).pack()
    ttk.Button(tab, text="🗑 삭제", command=remove_source).pack()
    tk.Label(tab, text="발신자 제외 (이름 - '성 이름' 그대로)").pack(pady=(10, 0))
    sender_input = ttk.Entry(tab, width=40)
    sender_input.pack()
    sender_listbox = tk.Listbox(tab, height=5)
    sender_listbox.pack(pady=5)
    def refresh_sender_list():
        sender_listbox.delete(0, tk.END)
        for s in copy_exclude_senders:
            sender_listbox.insert(tk.END, s)
    def add_sender():
        val = sender_input.get().strip()
        if val and val not in copy_exclude_senders:
            copy_exclude_senders.append(val)
            refresh_sender_list()
    def remove_sender():
        try:
            sel = sender_listbox.get(sender_listbox.curselection())
            copy_exclude_senders.remove(sel)
            refresh_sender_list()
        except:
            pass
    ttk.Button(tab, text="➕ 추가", command=add_sender).pack()
    ttk.Button(tab, text="🗑 삭제", command=remove_sender).pack()
    copy_var = tk.BooleanVar(value=False)
    def toggle_copy():
        global copy_enabled
        copy_enabled = copy_var.get()
    ttk.Checkbutton(tab, text="방배끼기 작동 ON/OFF", variable=copy_var, command=toggle_copy).pack(pady=5)
    refresh_source_list()
    refresh_sender_list()

def build_expert_tab(tab):
    container = ttk.Frame(tab)
    container.pack(fill="both", expand=True, padx=10, pady=10)
    global expert_accounts, expert_rooms, expert_names, expert_mode_enabled
    ttk.Label(container, text="전문가 셋팅", font=("Helvetica", 16, "bold")).pack(pady=10)
    mode_frame = ttk.Frame(container)
    mode_frame.pack(fill="x", pady=5)
    ttk.Label(mode_frame, text="전문가 배끼기 ON/OFF:").pack(side="left")
    expert_mode_var = tk.BooleanVar(value=expert_mode_enabled)
    def on_expert_toggle():
        global expert_mode_enabled
        expert_mode_enabled = expert_mode_var.get()
        update_expert_handlers()
    ttk.Checkbutton(mode_frame, variable=expert_mode_var, command=on_expert_toggle).pack(side="left", padx=5)
    acc_frame = ttk.LabelFrame(container, text="전문가 계정 목록")
    acc_frame.pack(fill="x", pady=5)
    acc_input_var = tk.StringVar()
    ttk.Entry(acc_frame, textvariable=acc_input_var, width=25).pack(side="left", padx=5)
    expert_acc_listbox = tk.Listbox(acc_frame, height=3)
    expert_acc_listbox.pack(side="left", padx=5)
    def refresh_expert_acc_list():
        expert_acc_listbox.delete(0, tk.END)
        for a in expert_accounts:
            expert_acc_listbox.insert(tk.END, a)
    def add_expert_account():
        val = acc_input_var.get().strip()
        if val and val not in expert_accounts:
            expert_accounts.append(val)
            if val not in copy_exclude_senders:
                copy_exclude_senders.append(val)
            refresh_expert_acc_list()
            acc_input_var.set("")
            update_expert_handlers()
    def remove_expert_account():
        try:
            sel = expert_acc_listbox.get(expert_acc_listbox.curselection())
            if sel in expert_accounts:
                expert_accounts.remove(sel)
                if sel in copy_exclude_senders:
                    copy_exclude_senders.remove(sel)
                refresh_expert_acc_list()
                update_expert_handlers()
        except:
            pass
    ttk.Button(acc_frame, text="➕ 추가", command=add_expert_account).pack(side="left", padx=5)
    ttk.Button(acc_frame, text="🗑 삭제", command=remove_expert_account).pack(side="left", padx=5)
    room_frame = ttk.LabelFrame(container, text="전문가 복사 방 ID 목록")
    room_frame.pack(fill="x", pady=5)
    room_input_var = tk.StringVar()
    ttk.Entry(room_frame, textvariable=room_input_var, width=25).pack(side="left", padx=5)
    expert_room_listbox = tk.Listbox(room_frame, height=3)
    expert_room_listbox.pack(side="left", padx=5)
    def refresh_expert_room_list():
        expert_room_listbox.delete(0, tk.END)
        for r in expert_rooms:
            expert_room_listbox.insert(tk.END, r)
    def add_expert_room():
        val = room_input_var.get().strip()
        if val and val not in expert_rooms:
            expert_rooms.append(val)
            refresh_expert_room_list()
            room_input_var.set("")
            update_expert_handlers()
    def remove_expert_room():
        try:
            sel = expert_room_listbox.get(expert_room_listbox.curselection())
            if sel in expert_rooms:
                expert_rooms.remove(sel)
                refresh_expert_room_list()
                update_expert_handlers()
        except:
            pass
    ttk.Button(room_frame, text="➕ 추가", command=add_expert_room).pack(side="left", padx=5)
    ttk.Button(room_frame, text="🗑 삭제", command=remove_expert_room).pack(side="left", padx=5)
    name_frame = ttk.LabelFrame(container, text="인식할 이름 목록")
    name_frame.pack(fill="x", pady=5)
    name_input_var = tk.StringVar()
    ttk.Entry(name_frame, textvariable=name_input_var, width=25).pack(side="left", padx=5)
    expert_name_listbox = tk.Listbox(name_frame, height=3)
    expert_name_listbox.pack(side="left", padx=5)
    def refresh_expert_name_list():
        expert_name_listbox.delete(0, tk.END)
        for nm in expert_names:
            expert_name_listbox.insert(tk.END, nm)
    def add_expert_name():
        nm = name_input_var.get().strip()
        if nm and nm not in expert_names:
            expert_names.append(nm)
            refresh_expert_name_list()
            name_input_var.set("")
            update_expert_handlers()
    def remove_expert_name():
        try:
            sel = expert_name_listbox.get(expert_name_listbox.curselection())
            if sel in expert_names:
                expert_names.remove(sel)
                refresh_expert_name_list()
                update_expert_handlers()
        except:
            pass
    ttk.Button(name_frame, text="➕ 추가", command=add_expert_name).pack(side="left", padx=5)
    ttk.Button(name_frame, text="🗑 삭제", command=remove_expert_name).pack(side="left", padx=5)
    refresh_expert_acc_list()
    refresh_expert_room_list()
    refresh_expert_name_list()

def build_account_management_tab(tab):
    container = ttk.Frame(tab)
    container.pack(fill="both", expand=True, padx=10, pady=10)
    top_frame = ttk.Frame(container)
    top_frame.pack(fill="x")
    ttk.Label(top_frame, text="계정관리", font=("Helvetica", 16, "bold")).pack(side="left", padx=5)
    ttk.Button(top_frame, text="계정 추가", command=lambda: open_add_account_window()).pack(side="right", padx=5)
    search_frame = ttk.Frame(container)
    search_frame.pack(fill="x", pady=5)
    ttk.Label(search_frame, text="계정 찾기: ").pack(side="left")
    account_search_var_management = tk.StringVar()
    search_entry_mgmt = ttk.Entry(search_frame, textvariable=account_search_var_management, width=30)
    search_entry_mgmt.pack(side="left", padx=5)
    ttk.Button(search_frame, text="찾기", command=lambda: refresh_account_list_mgmt()).pack(side="left", padx=5)
    main_frame = ttk.Frame(container)
    main_frame.pack(fill="both", expand=True)
    left_frame = ttk.Frame(main_frame)
    left_frame.pack(side="left", fill="both", expand=False)
    canvas = tk.Canvas(left_frame, width=400, height=400)
    canvas.pack(side="left", fill="y", expand=False)
    scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.configure(yscrollcommand=scrollbar.set)
    inner_frame = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=inner_frame, anchor="nw")
    def on_inner_configure(event):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner_frame.bind("<Configure>", on_inner_configure)
    right_frame = ttk.Frame(main_frame, relief="groove", borderwidth=1)
    right_frame.pack(side="left", fill="both", expand=True, padx=5)
    tk.Label(right_frame, text="해당 계정의 대화방 목록").pack(pady=5)
    chat_frame = ttk.Frame(right_frame)
    chat_frame.pack(fill="both", padx=5, pady=2)
    chat_scrollbar = ttk.Scrollbar(chat_frame, orient="vertical")
    chat_listbox = tk.Listbox(chat_frame, height=10, width=50, yscrollcommand=chat_scrollbar.set, exportselection=False)
    chat_scrollbar.config(command=chat_listbox.yview)
    chat_listbox.pack(side="left", fill="both", expand=True)
    chat_scrollbar.pack(side="right", fill="y")
    main_chat_frame = ttk.LabelFrame(right_frame, text="메인방 관리")
    main_chat_frame.pack(fill="x", padx=5, pady=5)
    main_chat_var = tk.BooleanVar(value=False)
    def toggle_main_chat():
        pass
    ttk.Checkbutton(main_chat_frame, text="메인방 끌어오기", variable=main_chat_var, command=toggle_main_chat).pack(anchor="w", padx=5, pady=3)
    main_chat_listbox = tk.Listbox(main_chat_frame, height=2, exportselection=False)
    main_chat_listbox.pack(fill="x", padx=5, pady=2)
    ttk.Button(main_chat_frame, text="메인방 등록", command=lambda: register_main_chat_mgmt()).pack(side="left", padx=5, pady=2)
    ttk.Button(main_chat_frame, text="메인방 삭제", command=lambda: remove_main_chat_mgmt()).pack(side="left", padx=5, pady=2)
    sub_chat_frame = ttk.LabelFrame(right_frame, text="서브방 관리")
    sub_chat_frame.pack(fill="x", padx=5, pady=5)
    sub_chat_var = tk.BooleanVar(value=False)
    def toggle_sub_chat():
        pass
    ttk.Checkbutton(sub_chat_frame, text="서브방 끌어오기", variable=sub_chat_var, command=toggle_sub_chat).pack(anchor="w", padx=5, pady=3)
    sub_chat_listbox = tk.Listbox(sub_chat_frame, height=5, exportselection=False)
    sub_chat_listbox.pack(fill="x", padx=5, pady=2)
    ttk.Button(sub_chat_frame, text="서브방 등록", command=lambda: register_sub_chat_mgmt()).pack(side="left", padx=5, pady=2)
    ttk.Button(sub_chat_frame, text="서브방 삭제", command=lambda: remove_sub_chat_mgmt()).pack(side="left", padx=5, pady=2)
    account_active_vars = {}
    current_selected_phone = [None]
    def load_account_chats_mgmt(phone):
        current_selected_phone[0] = phone
        chat_listbox.delete(0, tk.END)
        if phone not in clients:
            chat_listbox.insert(tk.END, "이 계정의 클라이언트가 준비되지 않음")
            refresh_main_chat_listbox_mgmt(phone)
            refresh_sub_chat_listbox_mgmt(phone)
            return
        c = clients[phone]
        loop = client_loops.get(phone)
        if not loop:
            chat_listbox.insert(tk.END, "이 계정의 event loop가 없음")
            refresh_main_chat_listbox_mgmt(phone)
            refresh_sub_chat_listbox_mgmt(phone)
            return
        async def fetch_dialogs():
            results = []
            async for d in c.iter_dialogs():
                results.append((d.name, d.id))
            return results
        future = asyncio.run_coroutine_threadsafe(fetch_dialogs(), loop)
        try:
            dialogs = future.result(timeout=10)
            if not dialogs:
                chat_listbox.insert(tk.END, "참여 중인 대화방이 없습니다.")
            else:
                for nm, cid in dialogs:
                    chat_listbox.insert(tk.END, f"{nm} (ID={cid})")
        except Exception as e:
            chat_listbox.insert(tk.END, f"불러오기 실패: {e}")
        refresh_main_chat_listbox_mgmt(phone)
        refresh_sub_chat_listbox_mgmt(phone)
    def parse_chat_id_from_string(s):
        import re
        match = re.search(r'ID=([\-0-9]+)', s)
        if match:
            try:
                return int(match.group(1))
            except:
                return None
        return None
    def get_chat_name_from_id(phone, cid):
        if phone not in clients:
            return "Unknown"
        c = clients[phone]
        loop = client_loops.get(phone)
        if not loop:
            return "Unknown"
        async def find_name():
            async for d in c.iter_dialogs():
                if d.id == cid:
                    return d.name
            return None
        future = asyncio.run_coroutine_threadsafe(find_name(), loop)
        try:
            name = future.result(timeout=5)
            return name if name else "Unknown"
        except:
            return "Unknown"
    def refresh_main_chat_listbox_mgmt(phone):
        main_chat_listbox.delete(0, tk.END)
        allacc = load_accounts()
        found = None
        for a in allacc:
            if a["phone"] == phone:
                found = a
                break
        if not found:
            return
        main_id = found.get("main_chat_id")
        if main_id:
            nm = get_chat_name_from_id(phone, main_id)
            main_chat_listbox.insert(tk.END, f"{nm} (ID={main_id})")
        else:
            main_chat_listbox.insert(tk.END, "등록된 메인방 없음")
    def refresh_sub_chat_listbox_mgmt(phone):
        sub_chat_listbox.delete(0, tk.END)
        allacc = load_accounts()
        found = None
        for a in allacc:
            if a["phone"] == phone:
                found = a
                break
        if not found:
            return
        sub_ids = found.get("subroom_ids", [])
        if not sub_ids:
            sub_chat_listbox.insert(tk.END, "등록된 서브방 없음")
        else:
            for sid in sub_ids:
                nm = get_chat_name_from_id(phone, sid)
                sub_chat_listbox.insert(tk.END, f"{nm} (ID={sid})")
    def register_main_chat_mgmt():
        if not current_selected_phone[0]:
            messagebox.showinfo("알림", "먼저 왼쪽 계정을 선택하세요.")
            return
        phone = current_selected_phone[0]
        try:
            sel = chat_listbox.get(chat_listbox.curselection())
        except:
            messagebox.showinfo("알림", "대화방 목록에서 선택하세요.")
            return
        cid = parse_chat_id_from_string(sel)
        if cid is None:
            messagebox.showerror("오류", "메인방 ID 파싱 실패")
            return
        allacc = load_accounts()
        for a in allacc:
            if a["phone"] == phone:
                a["main_chat_id"] = cid
                break
        save_accounts(allacc)
        refresh_main_chat_listbox_mgmt(phone)
        messagebox.showinfo("알림", f"{phone} 계정 메인방({cid}) 등록 완료.")
    def remove_main_chat_mgmt():
        if not current_selected_phone[0]:
            messagebox.showinfo("알림", "먼저 왼쪽 계정을 선택하세요.")
            return
        phone = current_selected_phone[0]
        allacc = load_accounts()
        for a in allacc:
            if a["phone"] == phone:
                if "main_chat_id" in a:
                    del a["main_chat_id"]
                break
        save_accounts(allacc)
        refresh_main_chat_listbox_mgmt(phone)
        messagebox.showinfo("알림", "메인방을 삭제했습니다.")
    def register_sub_chat_mgmt():
        if not current_selected_phone[0]:
            return
        phone = current_selected_phone[0]
        try:
            sel = chat_listbox.get(chat_listbox.curselection())
        except:
            messagebox.showinfo("알림", "대화방 목록에서 선택하세요.")
            return
        cid = parse_chat_id_from_string(sel)
        if cid is None:
            messagebox.showerror("오류", "서브방 ID 파싱 실패")
            return
        allacc = load_accounts()
        for a in allacc:
            if a["phone"] == phone:
                sub_ids = a.get("subroom_ids", [])
                if cid not in sub_ids:
                    sub_ids.append(cid)
                a["subroom_ids"] = sub_ids
                break
        save_accounts(allacc)
        refresh_sub_chat_listbox_mgmt(phone)
        messagebox.showinfo("알림", f"{phone} 계정 서브방({cid}) 등록 완료.")
    def remove_sub_chat_mgmt():
        if not current_selected_phone[0]:
            return
        phone = current_selected_phone[0]
        try:
            sel = sub_chat_listbox.get(sub_chat_listbox.curselection())
        except:
            return
        cid = parse_chat_id_from_string(sel)
        if cid is None:
            messagebox.showerror("오류", "서브방 ID 파싱 실패")
            return
        allacc = load_accounts()
        for a in allacc:
            if a["phone"] == phone:
                sub_ids = a.get("subroom_ids", [])
                if cid in sub_ids:
                    sub_ids.remove(cid)
                a["subroom_ids"] = sub_ids
                break
        save_accounts(allacc)
        refresh_sub_chat_listbox_mgmt(phone)
        messagebox.showinfo("알림", f"서브방({cid}) 삭제 완료.")
    def refresh_account_list_mgmt():
        for widget in inner_frame.winfo_children():
            widget.destroy()
        allacc = load_accounts()
        filter_str = account_search_var_management.get().strip()
        for idx, acc in enumerate(allacc, start=1):
            phone = acc["phone"]
            if filter_str and filter_str not in phone:
                continue
            row = ttk.Frame(inner_frame)
            row.pack(fill="x", pady=2, padx=2)
            def on_number_click(ph=phone):
                load_account_chats_mgmt(ph)
            num_btn = ttk.Button(row, text=f"{idx}번", width=6,
                                 command=lambda ph=phone: on_number_click(ph))
            num_btn.pack(side="left")
            ttk.Label(row, text=phone, width=18, anchor="center").pack(side="left", padx=5)
            var = tk.BooleanVar(value=True)
            account_active_map[phone] = True
            def on_check_changed(ph=phone, v=var):
                account_active_map[ph] = v.get()
            chk = ttk.Checkbutton(row, text="활성화", variable=var,
                                  command=lambda ph=phone, v=var: on_check_changed(ph, v))
            chk.pack(side="left", padx=5)
            def on_delete_account(ph=phone):
                confirm = messagebox.askyesno("계정 삭제", f"정말로 {ph} 계정을 삭제하시겠습니까?")
                if confirm:
                    old_list = load_accounts()
                    session_name = None
                    for a in old_list:
                        if a["phone"] == ph:
                            session_name = a.get("session_name", f"session_{ph}")
                            break
                    new_list = [x for x in old_list if x["phone"] != ph]
                    save_accounts(new_list)
                    messagebox.showinfo("알림", f"{ph} 계정이 삭제되었습니다.")
                    refresh_account_list_mgmt()
                    if session_name:
                        session_file = session_name + ".session"
                        if os.path.exists(session_file):
                            try:
                                os.remove(session_file)
                                print(f"[{ph}] 세션 파일({session_file}) 삭제 완료.")
                            except Exception as err:
                                print(f"[{ph}] 세션 파일({session_file}) 삭제 실패: {err}")
            del_btn = ttk.Button(row, text="삭제", command=lambda ph=phone: on_delete_account(ph))
            del_btn.pack(side="left", padx=5)
        canvas.update_idletasks()
        canvas.configure(scrollregion=canvas.bbox("all"))
    refresh_account_list_mgmt()

def build_alert_bot_tab_multi(tab):
    global alert_bot_enabled, alert_notify_chat
    container = ttk.Frame(tab)
    container.pack(fill="both", expand=True, padx=10, pady=10)
    def toggle_alert_bot():
        global alert_bot_enabled
        alert_bot_enabled = alert_bot_toggle.get()
        update_alert_handlers()
        print(f"[알림 봇 전체] -> {alert_bot_enabled}")
    alert_bot_toggle = tk.BooleanVar(value=False)
    ttk.Checkbutton(container, text="알림 봇 전체 기능 ON/OFF", variable=alert_bot_toggle,
                    command=toggle_alert_bot).pack(anchor="w", pady=5)
    notify_frame = ttk.Frame(container)
    notify_frame.pack(fill="x", pady=2)
    ttk.Label(notify_frame, text="알림 채팅방 ID: ").pack(side="left")
    alert_chat_var = tk.StringVar()
    if alert_notify_chat:
        alert_chat_var.set(str(alert_notify_chat))
    alert_chat_entry = ttk.Entry(notify_frame, textvariable=alert_chat_var, width=25)
    alert_chat_entry.pack(side="left", padx=5)
    def add_alert_chat():
        global alert_notify_chat
        try:
            new_id = int(alert_chat_var.get().strip())
            alert_notify_chat = new_id
            s = load_alert_settings()
            s["alert_notify_chat"] = alert_notify_chat
            save_alert_settings(s)
            messagebox.showinfo("알림", f"알림 채팅방 ID {alert_notify_chat} 등록 완료.")
            refresh_current_notify_chat_label()
        except:
            messagebox.showerror("오류", "유효한 채팅방 ID를 입력하세요.")
    def remove_alert_chat():
        global alert_notify_chat
        alert_notify_chat = None
        alert_chat_var.set("")
        s = load_alert_settings()
        s["alert_notify_chat"] = None
        save_alert_settings(s)
        messagebox.showinfo("알림", "알림 채팅방 ID 삭제 완료.")
        refresh_current_notify_chat_label()
    ttk.Button(notify_frame, text="➕ 등록", command=add_alert_chat).pack(side="left", padx=3)
    ttk.Button(notify_frame, text="🗑 삭제", command=remove_alert_chat).pack(side="left", padx=3)
    label_current_id = ttk.Label(notify_frame, text="")
    label_current_id.pack(side="left", padx=10)
    def refresh_current_notify_chat_label():
        if alert_notify_chat:
            label_current_id.config(text=f"현재 등록: {alert_notify_chat}")
        else:
            label_current_id.config(text="(등록된 알림 채팅방 없음)")
    refresh_current_notify_chat_label()
    top_search_frame = ttk.Frame(container)
    top_search_frame.pack(fill="x", pady=2)
    ttk.Label(top_search_frame, text="계정 찾기: ").pack(side="left")
    alert_search_var = tk.StringVar()
    alert_search_entry = ttk.Entry(top_search_frame, textvariable=alert_search_var, width=30)
    alert_search_entry.pack(side="left", padx=5)
    def search_alert_accounts():
        refresh_all_accounts_list()
    ttk.Button(top_search_frame, text="찾기", command=search_alert_accounts).pack(side="left", padx=5)
    main_frame = ttk.Frame(container)
    main_frame.pack(fill="both", expand=True)
    left_frame = ttk.Frame(main_frame)
    left_frame.pack(side="left", fill="both", expand=False, padx=5)
    ttk.Label(left_frame, text="모든 계정 목록").pack()
    all_accounts_listbox = tk.Listbox(left_frame, width=25, height=12, exportselection=False)
    all_accounts_listbox.pack(pady=5)
    ttk.Label(left_frame, text="감시할 계정 목록").pack()
    watch_accounts_listbox = tk.Listbox(left_frame, width=25, height=6, exportselection=False)
    watch_accounts_listbox.pack(pady=5)
    btn_frame = ttk.Frame(left_frame)
    btn_frame.pack()
    def add_to_watchlist():
        try:
            sel = all_accounts_listbox.get(all_accounts_listbox.curselection())
        except:
            return
        phone = sel.strip()
        acc_list = load_accounts()
        for a in acc_list:
            if a["phone"] == phone:
                a["alert_monitor"] = True
                if "alert_rooms" not in a:
                    a["alert_rooms"] = []
                break
        save_accounts(acc_list)
        refresh_watch_accounts_list()
        update_alert_handlers()
    def remove_from_watchlist():
        try:
            sel = watch_accounts_listbox.get(watch_accounts_listbox.curselection())
        except:
            return
        phone = sel.strip()
        acc_list = load_accounts()
        for a in acc_list:
            if a["phone"] == phone:
                a["alert_monitor"] = False
                break
        save_accounts(acc_list)
        refresh_watch_accounts_list()
        update_alert_handlers()
    ttk.Button(btn_frame, text="➕ 추가", command=add_to_watchlist).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="🗑 삭제", command=remove_from_watchlist).pack(side="left", padx=5)
    right_frame = ttk.Frame(main_frame, relief="groove", borderwidth=1)
    right_frame.pack(side="left", fill="both", expand=True, padx=5)
    ttk.Label(right_frame, text="감시 계정의 대화방 목록").pack(pady=3)
    chat_frame = ttk.Frame(right_frame)
    chat_frame.pack(fill="both", padx=5, pady=2)
    chat_scroll = ttk.Scrollbar(chat_frame, orient="vertical")
    chat_listbox = tk.Listbox(chat_frame, height=10, yscrollcommand=chat_scroll.set, exportselection=False)
    chat_scroll.config(command=chat_listbox.yview)
    chat_listbox.pack(side="left", fill="both", expand=True)
    chat_scroll.pack(side="right", fill="y")
    ttk.Label(right_frame, text="감시할 방 목록").pack(pady=3)
    watch_room_frame = ttk.Frame(right_frame)
    watch_room_frame.pack(fill="both", padx=5, pady=2)
    watch_room_scroll = ttk.Scrollbar(watch_room_frame, orient="vertical")
    watch_room_listbox = tk.Listbox(watch_room_frame, height=5, yscrollcommand=watch_room_scroll.set, exportselection=False)
    watch_room_scroll.config(command=watch_room_listbox.yview)
    watch_room_listbox.pack(side="left", fill="both", expand=True)
    watch_room_scroll.pack(side="right", fill="y")
    def parse_chat_id_from_string(s):
        import re
        match = re.search(r'ID=([\-0-9]+)', s)
        if match:
            try:
                return int(match.group(1))
            except:
                return None
        return None
    def add_watch_room():
        try:
            selacc = watch_accounts_listbox.get(watch_accounts_listbox.curselection())
            selchat = chat_listbox.get(chat_listbox.curselection())
        except:
            return
        phone = selacc.strip()
        cid = parse_chat_id_from_string(selchat)
        if cid is None:
            messagebox.showerror("오류", "방 ID 파싱 실패")
            return
        acc_list = load_accounts()
        for a in acc_list:
            if a["phone"] == phone:
                rlist = a.get("alert_rooms", [])
                if cid not in rlist:
                    rlist.append(cid)
                a["alert_rooms"] = rlist
                break
        save_accounts(acc_list)
        refresh_watch_rooms()
    def remove_watch_room():
        try:
            selacc = watch_accounts_listbox.get(watch_accounts_listbox.curselection())
            selchat = watch_room_listbox.get(watch_room_listbox.curselection())
        except:
            return
        phone = selacc.strip()
        cid = parse_chat_id_from_string(selchat)
        if cid is None:
            messagebox.showerror("오류", "방 ID 파싱 실패")
            return
        acc_list = load_accounts()
        for a in acc_list:
            if a["phone"] == phone:
                rlist = a.get("alert_rooms", [])
                if cid in rlist:
                    rlist.remove(cid)
                a["alert_rooms"] = rlist
                break
        save_accounts(acc_list)
        refresh_watch_rooms()
    room_btn_frame = ttk.Frame(right_frame)
    room_btn_frame.pack(pady=5)
    ttk.Button(room_btn_frame, text="➕ 추가", command=add_watch_room).pack(side="left", padx=5)
    ttk.Button(room_btn_frame, text="🗑 삭제", command=remove_watch_room).pack(side="left", padx=5)
    def refresh_chat_listbox_for_account(phone):
        chat_listbox.delete(0, tk.END)
        if phone not in clients:
            chat_listbox.insert(tk.END, "해당 계정 클라이언트가 준비되지 않음")
            return
        c = clients[phone]
        loop = client_loops.get(phone)
        if not loop:
            chat_listbox.insert(tk.END, "해당 계정의 event loop 없음")
            return
        async def fetch_dialogs():
            results = []
            async for d in c.iter_dialogs():
                results.append((d.name, d.id))
            return results
        future = asyncio.run_coroutine_threadsafe(fetch_dialogs(), loop)
        try:
            dialogs = future.result(timeout=10)
            if not dialogs:
                chat_listbox.insert(tk.END, "대화방 없음")
            else:
                for nm, cid in dialogs:
                    chat_listbox.insert(tk.END, f"{nm} (ID={cid})")
        except Exception as e:
            chat_listbox.insert(tk.END, f"불러오기 실패: {e}")
    def refresh_all_accounts_list():
        all_accounts_listbox.delete(0, tk.END)
        filter_str = alert_search_var.get().strip()
        for acc in load_accounts():
            ph = acc["phone"]
            if filter_str and filter_str not in ph:
                continue
            all_accounts_listbox.insert(tk.END, ph)
    def refresh_watch_accounts_list():
        watch_accounts_listbox.delete(0, tk.END)
        for acc in load_accounts():
            if acc.get("alert_monitor", False):
                watch_accounts_listbox.insert(tk.END, acc["phone"])
        if watch_accounts_listbox.size() > 0:
            if not watch_accounts_listbox.curselection():
                watch_accounts_listbox.select_set(0)
                watch_accounts_listbox.event_generate("<<ListboxSelect>>")
    def refresh_chat_listbox():
        chat_listbox.delete(0, tk.END)
        try:
            selacc = watch_accounts_listbox.get(watch_accounts_listbox.curselection())
        except:
            return
        phone = selacc.strip()
        refresh_chat_listbox_for_account(phone)
    def refresh_watch_rooms():
        watch_room_listbox.delete(0, tk.END)
        try:
            selacc = watch_accounts_listbox.get(watch_accounts_listbox.curselection())
        except:
            return
        phone = selacc.strip()
        acc = get_account_by_phone(phone)
        if not acc:
            return
        rlist = acc.get("alert_rooms", [])
        if not rlist:
            watch_room_listbox.insert(tk.END, "감시할 방 없음")
        else:
            if phone not in clients:
                for cid in rlist:
                    watch_room_listbox.insert(tk.END, f"ID={cid}")
                return
            c = clients[phone]
            loop = client_loops.get(phone)
            async def find_name(cid):
                async for d in c.iter_dialogs():
                    if d.id == cid:
                        return d.name
                return None
            for cid in rlist:
                future = asyncio.run_coroutine_threadsafe(find_name(cid), loop)
                nm = "Unknown"
                try:
                    res = future.result(timeout=5)
                    if res:
                        nm = res
                except:
                    pass
                watch_room_listbox.insert(tk.END, f"{nm} (ID={cid})")
    def on_watch_account_select(e):
        refresh_chat_listbox()
        refresh_watch_rooms()
    watch_accounts_listbox.bind("<<ListboxSelect>>", on_watch_account_select)
    def do_refresh():
        refresh_watch_accounts_list()
        refresh_chat_listbox()
        refresh_watch_rooms()
    global refresh_watch_accounts_list_global
    refresh_watch_accounts_list_global = do_refresh
    refresh_all_accounts_list()
    do_refresh()

def update_expert_handlers():
    global expert_mode_enabled, expert_accounts, expert_event_handlers, expert_handler_registered
    for phone, hs in list(expert_event_handlers.items()):
        new_h, edit_h, del_h = hs
        client = clients.get(phone)
        if client:
            client.remove_event_handler(new_h, events.NewMessage)
            client.remove_event_handler(edit_h, events.MessageEdited)
            client.remove_event_handler(del_h, events.MessageDeleted)
        expert_event_handlers.pop(phone, None)
        expert_handler_registered.discard(phone)
    if expert_mode_enabled:
        for phone in expert_accounts:
            client = clients.get(phone)
            if not client or phone in expert_handler_registered:
                continue
            new_h = make_expert_handler(phone)
            client.add_event_handler(new_h, events.NewMessage)
            edit_h = make_expert_edit_handler(phone)
            client.add_event_handler(edit_h, events.MessageEdited(func=lambda e: e.chat_id in copy_source_chats))
            del_h = make_expert_delete_handler(phone)
            client.add_event_handler(del_h, events.MessageDeleted(func=lambda e: e.chat_id in copy_source_chats))
            expert_event_handlers[phone] = (new_h, edit_h, del_h)
            expert_handler_registered.add(phone)

def make_expert_handler(phone):
    async def handler(event):
        await expert_new_message_handler(event, phone)
    return handler

def make_expert_edit_handler(phone):
    async def handler(event):
        key = (phone, event.chat_id, event.id)
        if key not in delete_map:
            return
        for (cid, fwd_id) in delete_map[key]:
            try:
                await clients[phone].edit_message(
                    cid, fwd_id, event.raw_text,
                    formatting_entities=event.message.entities,
                    file=event.media if event.media else None
                )
            except Exception as e:
                print(f"[전문가 편집 오류] {e}")
    return handler

def make_expert_delete_handler(phone):
    async def handler(event):
        key = (phone, event.chat_id, event.id)
        if key not in delete_map:
            return
        for (cid, fwd_id) in delete_map[key]:
            try:
                await clients[phone].delete_messages(cid, fwd_id)
            except Exception as e:
                print(f"[전문가 삭제 오류] {e}")
        delete_map.pop(key, None)
    return handler

async def expert_new_message_handler(event, phone):
    global expert_mode_enabled, expert_names
    if not expert_mode_enabled or not expert_names:
        return
    client = clients.get(phone)
    if not client:
        return
    acc = get_account_by_phone(phone)
    if not acc or "main_chat_id" not in acc:
        return
    if copy_source_chats and event.chat_id not in copy_source_chats:
        return
    sender = await event.get_sender()
    first_n = sender.first_name or ""
    last_n = sender.last_name or ""
    display_name = (last_n + " " + first_n).strip() or sender.username or ""
    if display_name not in expert_names:
        return
    try:
        ent_main = await client.get_input_entity(acc["main_chat_id"])
        if event.text and not event.media:
            sent_main = await client.send_message(
                ent_main, event.raw_text,
                formatting_entities=event.message.entities
            )
        else:
            sent_main = await client.send_file(
                ent_main, file=event.media,
                caption=event.raw_text,
                formatting_entities=event.message.entities if event.raw_text else None
            )
        key = (phone, event.chat_id, event.id)
        delete_map.setdefault(key, []).append((acc["main_chat_id"], sent_main.id))
        subrooms = acc.get("subroom_ids", [])
        if subrooms:
            await forward_to_subrooms_expert(client, acc, key, sent_main, target_rooms=subrooms)()
    except Exception as e:
        print(f"[전문가 복사 오류] {e}")

def add_copy_handler(client, phone):
    global copy_handler_registered, expert_accounts
    if phone in expert_accounts:
        return
    if phone in copy_handler_registered:
        return
    @client.on(events.NewMessage(func=lambda e: e.chat_id in copy_source_chats))
    async def copy_new_msg(e):
        if not copy_enabled:
            return
        sender = await e.get_sender()
        sender_id = sender.id
        first_n = sender.first_name or ""
        last_n  = sender.last_name or ""
        sender_fullname = (last_n + " " + first_n).strip() or sender.username or ""
        if sender_fullname in copy_exclude_senders:
            copy_sender_mapping.pop(sender_id, None)
            return
        allacc = load_accounts()
        valid_acc = [a for a in allacc if a["phone"] not in copy_exclude_senders]
        if not valid_acc:
            return
        if sender_id in copy_sender_mapping:
            tgt_phone = copy_sender_mapping[sender_id]
            if not any(a["phone"] == tgt_phone for a in valid_acc):
                del copy_sender_mapping[sender_id]
        if sender_id not in copy_sender_mapping:
            assigned = set(copy_sender_mapping.values())
            avail = [a for a in valid_acc if a["phone"] not in assigned]
            chosen = random.choice(avail) if avail else random.choice(valid_acc)
            copy_sender_mapping[sender_id] = chosen["phone"]
        else:
            chosen_phone = copy_sender_mapping[sender_id]
            chosen = next((a for a in valid_acc if a["phone"] == chosen_phone), None)
            if not chosen:
                chosen = random.choice(valid_acc)
                copy_sender_mapping[sender_id] = chosen["phone"]
        if phone != copy_sender_mapping[sender_id]:
            return
        chosen_phone = copy_sender_mapping[sender_id]
        tgt_client = clients.get(chosen_phone)
        tgt_loop = client_loops.get(chosen_phone)
        if not tgt_client or not tgt_loop:
            return
        async def forward_msg():
            try:
                acc_details = get_account_by_phone(chosen_phone)
                main_id = acc_details.get("main_chat_id")
                if not main_id:
                    return
                ent_main = await tgt_client.get_input_entity(main_id)
                if e.media:
                    sent = await tgt_client.send_file(
                        ent_main, file=e.media,
                        caption=e.raw_text,
                        formatting_entities=e.message.entities if e.raw_text else None
                    )
                else:
                    sent = await tgt_client.send_message(
                        ent_main, e.raw_text,
                        formatting_entities=e.message.entities
                    )
                copy_msg_mapping[(sender_id, e.id)] = (chosen_phone, sent.id)
                subrooms = acc_details.get("subroom_ids", [])
                if subrooms:
                    await forward_to_subrooms(tgt_client, acc_details, sent, target_rooms=subrooms)()
            except Exception as ex:
                print(f"[방배끼기 전송 오류] {ex}")
        asyncio.run_coroutine_threadsafe(forward_msg(), tgt_loop)
    @client.on(events.MessageEdited(func=lambda e: e.chat_id in copy_source_chats))
    async def copy_edit_msg(e):
        if not copy_enabled:
            return
        sender = await e.get_sender()
        key = (sender.id, e.id)
        mapping = copy_msg_mapping.get(key)
        if not mapping:
            return
        tgt_phone, fwd_id = mapping
        tgt_client = clients.get(tgt_phone)
        if not tgt_client:
            return
        acc_details = get_account_by_phone(tgt_phone)
        main_id = acc_details.get("main_chat_id")
        if not main_id:
            return
        try:
            await tgt_client.edit_message(
                main_id, fwd_id,
                e.raw_text,
                formatting_entities=e.message.entities,
                file=e.media if e.media else None
            )
        except Exception as ex:
            print(f"[방배끼기 편집 오류] {ex}")
        key2 = (tgt_phone, fwd_id)
        if key2 in delete_map:
            for (sid, smid) in delete_map[key2]:
                try:
                    await tgt_client.edit_message(
                        sid, smid,
                        e.raw_text,
                        formatting_entities=e.message.entities,
                        file=e.media if e.media else None
                    )
                except Exception as ex2:
                    print(f"[방배끼기 서브 편집 오류] {ex2}")
    @client.on(events.MessageDeleted(func=lambda e: e.chat_id in copy_source_chats))
    async def copy_del_msg(e):
        sender = None
        try:
            sender = await e.get_sender()
        except:
            return
        for del_id in e.deleted_ids:
            key = (sender.id, del_id)
            mapping = copy_msg_mapping.get(key)
            if not mapping:
                continue
            tgt_phone, fwd_id = mapping
            tgt_client = clients.get(tgt_phone)
            if not tgt_client:
                continue
            acc_details = get_account_by_phone(tgt_phone)
            main_id = acc_details.get("main_chat_id")
            if not main_id:
                continue
            try:
                await tgt_client.delete_messages(main_id, fwd_id)
            except Exception as ex:
                print(f"[방배끼기 메인 삭제 오류] {ex}")
            key2 = (tgt_phone, fwd_id)
            if key2 in delete_map:
                for (sid, smid) in delete_map[key2]:
                    try:
                        await tgt_client.delete_messages(sid, smid)
                    except Exception as ex2:
                        print(f"[방배끼기 서브 삭제 오류] {ex2}")
                delete_map.pop(key2, None)
    copy_handler_registered.add(phone)

def run_copy_monitor():
    time.sleep(5)
    if not clients:
        print("방배끼기: 등록된 클라이언트가 없습니다.")
        return
    for phone, client in clients.items():
        add_copy_handler(client, phone)

def open_add_account_window():
    wizard = tk.Toplevel()
    wizard.title("계정 추가")
    wizard.geometry("350x470")
    phone_label = ttk.Label(wizard, text="전화번호:")
    phone_label.pack(pady=2)
    phone_entry = ttk.Entry(wizard, width=30)
    phone_entry.pack(pady=2)
    api_id_label = ttk.Label(wizard, text="API ID:")
    api_id_label.pack(pady=2)
    api_id_entry = ttk.Entry(wizard, width=30)
    api_id_entry.pack(pady=2)
    api_hash_label = ttk.Label(wizard, text="API Hash:")
    api_hash_label.pack(pady=2)
    api_hash_entry = ttk.Entry(wizard, width=30)
    api_hash_entry.pack(pady=2)
    password_label = ttk.Label(wizard, text="Password:")
    password_label.pack(pady=2)
    password_entry = ttk.Entry(wizard, width=30, show="*")
    password_entry.pack(pady=2)
    session_label = ttk.Label(wizard, text="Session Name:")
    session_label.pack(pady=2)
    session_entry = ttk.Entry(wizard, width=30)
    session_entry.pack(pady=2)
    def check_duplicate_session():
        session_val = session_entry.get().strip()
        if not session_val:
            messagebox.showwarning("경고", "Session Name을 입력하세요.")
            return
        accounts = load_accounts()
        is_dup = any(acc.get("session_name", f"session_{acc['phone']}") == session_val for acc in accounts)
        if is_dup:
            messagebox.showerror("중복", "이미 사용 중인 세션 이름입니다.")
        else:
            messagebox.showinfo("확인", "사용 가능한 세션 이름입니다.")
    dup_btn = ttk.Button(wizard, text="세션 이름 중복확인", command=check_duplicate_session)
    dup_btn.pack(pady=3)
    auth_btn = ttk.Button(wizard, text="인증번호 호출")
    auth_btn.pack(pady=5)
    code_label = ttk.Label(wizard, text="인증코드:")
    code_label.pack(pady=2)
    code_entry = ttk.Entry(wizard, width=30)
    code_entry.pack(pady=2)
    attempts = 0
    auth_client = [None]
    auth_loop = [None]
    def start_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()
    def call_auth_code():
        phone_val = phone_entry.get().strip()
        api_id_val = api_id_entry.get().strip()
        api_hash_val = api_hash_entry.get().strip()
        if not phone_val or not api_id_val or not api_hash_val:
            messagebox.showwarning("경고", "전화번호/API ID/API Hash를 입력하세요.")
            return
        try:
            _ = int(api_id_val)
        except:
            messagebox.showwarning("경고", "API ID는 숫자여야 합니다.")
            return
        session_val = session_entry.get().strip()
        if not session_val:
            messagebox.showwarning("경고", "Session Name을 입력하세요.")
            return
        if not auth_loop[0]:
            loop = asyncio.new_event_loop()
            auth_loop[0] = loop
            t = Thread(target=start_loop, args=(loop,), daemon=True)
            t.start()
        async def send_code():
            c = TelegramClient(session_val, int(api_id_val), api_hash_val)
            await c.connect()
            await c.send_code_request(phone_val)
            auth_client[0] = c
        future = asyncio.run_coroutine_threadsafe(send_code(), auth_loop[0])
        try:
            future.result()
            messagebox.showinfo("알림", f"{phone_val}에 인증번호 요청. 입력 후 '다음' 버튼 클릭.")
        except Exception as e:
            messagebox.showerror("오류", f"인증번호 호출 실패: {e}")
    auth_btn.config(command=call_auth_code)
    def attempt_login():
        nonlocal attempts
        phone_val = phone_entry.get().strip()
        api_id_val = api_id_entry.get().strip()
        api_hash_val = api_hash_entry.get().strip()
        pwd_val = password_entry.get().strip()
        code_val = code_entry.get().strip()
        if not (phone_val and api_id_val and api_hash_val and code_val):
            messagebox.showwarning("경고", "모든 필드를 입력하세요.")
            return
        try:
            _ = int(api_id_val)
        except:
            messagebox.showwarning("경고", "API ID는 숫자만 가능합니다.")
            return
        if not auth_client[0]:
            messagebox.showwarning("경고", "먼저 인증번호 호출을 진행하세요.")
            return
        async def do_signin():
            c = auth_client[0]
            await c.connect()
            try:
                await c.sign_in(phone_val, code_val)
            except Exception as e:
                await c.sign_in(password=pwd_val)
            await c.disconnect()
        future = asyncio.run_coroutine_threadsafe(do_signin(), auth_loop[0])
        try:
            future.result()
            new_acc = {
                "phone": phone_val,
                "api_id": int(api_id_val),
                "api_hash": api_hash_val,
                "password": pwd_val,
                "session_name": session_entry.get().strip(),
                "alert_monitor": False,
                "alert_rooms": []
            }
            curr = load_accounts()
            curr.append(new_acc)
            save_accounts(curr)
            if auth_loop[0]:
                auth_loop[0].call_soon_threadsafe(auth_loop[0].stop)
                auth_loop[0] = None
            messagebox.showinfo("성공", f"{phone_val} 계정 인증 완료! (세션이 저장되어 재인증 불필요)")
            wizard.destroy()
        except Exception as e:
            attempts += 1
            if attempts >= 2:
                messagebox.showerror("실패", f"{phone_val} 인증 2회 실패로 추가 취소.\n{e}")
                wizard.destroy()
            else:
                messagebox.showwarning("실패", f"인증 실패: {e}")
    next_btn = ttk.Button(wizard, text="다음", command=attempt_login)
    next_btn.pack(pady=10)
    wizard.mainloop()

refresh_watch_accounts_list_global = None

def run_main():
    Thread(target=start_gui).start()

def start_gui():
    global root, alert_notify_chat, admin_enabled
    load_admin_data()
    # 관리자 기능 시작 시 기본 OFF
    admin_enabled = False
    save_admin_data()
    settings = load_alert_settings()
    alert_notify_chat = settings.get("alert_notify_chat", None)
    root = tk.Tk()
    root.title("텔레그램 자동화 프로그램")
    root.geometry("900x900")
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    tab_main = ttk.Frame(notebook)
    tab_copy = ttk.Frame(notebook)
    tab_expert = ttk.Frame(notebook)
    tab_account = ttk.Frame(notebook)
    tab_alert = ttk.Frame(notebook)
    tab_admin = ttk.Frame(notebook)  # 관리자 관리 탭
    notebook.add(tab_main, text="메인")
    notebook.add(tab_copy, text="방배끼기")
    notebook.add(tab_expert, text="전문가 셋팅")
    notebook.add(tab_account, text="계정관리")
    notebook.add(tab_alert, text="방 알림 봇")
    notebook.add(tab_admin, text="관리자 관리")
    build_main_tab(tab_main)
    build_copy_tab(tab_copy)
    build_expert_tab(tab_expert)
    build_account_management_tab(tab_account)
    build_alert_bot_tab_multi(tab_alert)
    build_admin_tab(tab_admin)
    root.mainloop()

if __name__ == "__main__":
    ensure_config_files()
    run_main()