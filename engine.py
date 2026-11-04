# -*- coding: utf-8 -*-
"""
智能课表引擎
────────────────────────────────────────────────────────
1) 自然语言指令解析：把"直接输入的文字"翻译成结构化调整操作
   （放假 / 调休补课 / 停课 / 调课 / 换教室）
2) 放假通知全文解析：从 OCR 文本中抽取调休规则
3) 课表计算：综合基础课表 + 调整规则，算出任意一天/一周"实际怎么上"
"""
import re
import json
import uuid
from datetime import datetime, timedelta, date

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
CN2NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
DEFAULT_YEAR = 2026

# ────────────────────────── 基础工具 ──────────────────────────


def new_id(prefix="adj"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def parse_week_range(s):
    """'2-9,11,13-16' -> {2,3,...,9,11,13,...16}"""
    weeks = set()
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                weeks |= set(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                weeks.add(int(part))
            except ValueError:
                pass
    return weeks


def week_monday(term_start: date, week: int) -> date:
    return term_start + timedelta(days=7 * (week - 1))


def week_of(d: date, term_start: date):
    """该日期属于第几教学周（term_start 为第 1 周周一）"""
    delta = (d - term_start).days
    if delta < 0:
        return None
    return delta // 7 + 1


def to_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return datetime.strptime(d[:10], "%Y-%m-%d").date()
    return d


# ────────────────────────── 文本要素抽取 ──────────────────────────

DATE_RE = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
RANGE_RE = re.compile(
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*"
    r"(?:至|到|—|–|-|~|～)\s*"
    r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"
)
WEEK_RE = re.compile(r"第\s*(\d{1,2})\s*周")
WEEK_RANGE_RE = re.compile(r"第\s*(\d{1,2})\s*[-—–~～至到]\s*(\d{1,2})\s*周")
WEEKDAY_RE = re.compile(r"(?:星期|周)\s*([一二三四五六日天])")
SEC_RANGE_RE = re.compile(r"第?\s*(\d{1,2})\s*(?:[-—–~～至到]\s*(\d{1,2})\s*)?节")
# 注意：必须排除「周」「月」后缀，且第二个数字后不能紧跟数字，否则 "10-12周" 会被匹配成 "10-1"
SEC_PLAIN_RE = re.compile(r"(\d{1,2})\s*[-—–~～]\s*(\d{1,2})(?!\d)(?!\s*月)(?!\s*周)")
ROOM_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z]*?(?:楼|馆|厅|室|[A-Za-z]{1,3}\d{2,4})[\u4e00-\u9fa5A-Za-z0-9]*)")

REL_DAY_RE = re.compile(r"(今天|明日|明天|后天|大后天)")
REL_WEEK_RE = re.compile(r"(本周|这周|下周|下下周)\s*(?:星期|周)?\s*([一二三四五六日天])?")


def _mk_date(month, day, year=None):
    y = int(year) if year else DEFAULT_YEAR
    try:
        return date(y, int(month), int(day))
    except ValueError:
        return None


def extract_dates(text):
    """抽取文本中出现的全部日期（去重、保序）"""
    out, seen = [], set()
    for m in DATE_RE.finditer(text):
        d = _mk_date(m.group(2), m.group(3), m.group(1))
        if d and d.strftime("%Y-%m-%d") not in seen:
            seen.add(d.strftime("%Y-%m-%d"))
            out.append(d)
    return out


def extract_ranges(text):
    """抽取日期区间，返回 [(start_date, end_date)]"""
    out = []
    for m in RANGE_RE.finditer(text):
        s = _mk_date(m.group(2), m.group(3), m.group(1))
        e = _mk_date(m.group(5), m.group(6), m.group(4))
        if s and e and e >= s:
            out.append((s, e))
    return out


def extract_weekday(text):
    m = WEEKDAY_RE.search(text)
    return CN2NUM[m.group(1)] if m else None


def extract_week(text):
    """返回周次列表：支持「第5周」和「第10-12周」；没有则返回 None（用 list 便于 JSON 序列化）"""
    m = WEEK_RANGE_RE.search(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a <= 30 and 1 <= b <= 30 and b >= a:
            return list(range(a, b + 1))
    m = WEEK_RE.search(text)
    if m:
        w = int(m.group(1))
        if 1 <= w <= 30:
            return [w]
    return None


def extract_sections(text):
    """返回 (start, end)；没有则返回 (None, None)"""
    m = SEC_RANGE_RE.search(text)
    if m:
        s = int(m.group(1))
        e = int(m.group(2)) if m.group(2) else s
        if 1 <= s <= 12 and 1 <= e <= 12 and e >= s:
            return s, e
    m = SEC_PLAIN_RE.search(text)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if 1 <= s <= 12 and 1 <= e <= 12 and e >= s:
            return s, e
    return None, None


def extract_relative_date(text, today=None):
    """今天 / 明天 / 下周三 等相对日期"""
    today = today or date.today()
    m = REL_DAY_RE.search(text)
    if m:
        return {"今天": 0, "明日": 1, "明天": 1, "后天": 2, "大后天": 3}[m.group(1)] and today + timedelta(
            days={"今天": 0, "明日": 1, "明天": 1, "后天": 2, "大后天": 3}[m.group(1)]
        )
    m = REL_WEEK_RE.search(text)
    if m:
        base_monday = today - timedelta(days=today.weekday())
        offset = {"本周": 0, "这周": 0, "下周": 1, "下下周": 2}[m.group(1)]
        if m.group(2):
            target = CN2NUM[m.group(2)]
            return base_monday + timedelta(days=7 * offset + target - 1)
        return base_monday + timedelta(days=7 * offset)
    return None


def course_core(name):
    """去掉「（1班）」「班」等后缀，得到课程核心名"""
    core = name or ""
    for _ in range(3):
        core = re.sub(r"[（(].*?[)）]", "", core)
        core = re.sub(r"班$", "", core).strip()
    return core


FUZZY_MIN_CHARS = 3   # 3 字以上子串直接认作简称
SHORT_PRE = "的把将请在从于对为节次周"
SHORT_POST = "课节调改停放到在换挪移上下本这周第明今后前 "


def _short_ok(sub, text):
    """两字简称只在上下文明显是课程名时才认（如「的物流调」「思政课」）"""
    for m in re.finditer(re.escape(sub), text):
        i, j = m.start(), m.end()
        pre = text[i - 1] if i > 0 else ""
        post = text[j] if j < len(text) else ""
        pre_ok = (not pre) or pre in SHORT_PRE or not re.match(r"[\u4e00-\u9fa5]", pre)
        post_ok = (not post) or post in SHORT_POST or not re.match(r"[\u4e00-\u9fa5]", post)
        if pre_ok and post_ok:
            return True
    return False


def course_slot(name, courses):
    """课程在课表里的默认时段（用于省略原时间时自动补齐）"""
    for c in courses:
        if (c.get("course_name") or "").strip() == name:
            out = {}
            if c.get("weekday"):
                out["weekday"] = int(c["weekday"])
            if c.get("start_section"):
                out["start"] = int(c["start_section"])
            if c.get("end_section"):
                out["end"] = int(c["end_section"])
            return out
    return {}


def match_course(text, courses, with_candidates=False):
    """
    在文本中匹配已知课程名，返回课程全名。

    匹配优先级：
      1. 课程全名出现在文本里（如「机器学习与数据分析班（1班）」）
      2. 去掉班级后缀的核心名出现（如「机器学习与数据分析」）
      3. 核心名的连续子串出现，即学生常用的简称（如「机器学习」「运筹学」「物流」）

    命中字数相同时若对应多门课，视为歧义：返回 None，并把候选放进第二个返回值。
    """
    hits = []           # [(命中的字数, 课程全名)]
    seen = set()
    for c in courses:
        name = (c.get("course_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        core = course_core(name)
        if name in text:
            hits.append((len(name), name))
        elif core and core in text:
            hits.append((len(core), name))
        elif len(core) >= 2:
            # 从长到短找最长的、确实出现在文本中的子串
            sub_len = 0
            for L in range(len(core) - 1, 1, -1):
                hit = False
                for i in range(len(core) - L + 1):
                    sub = core[i:i + L]
                    if sub not in text:
                        continue
                    if L >= FUZZY_MIN_CHARS or _short_ok(sub, text):
                        hit = True
                        break
                if hit:
                    sub_len = L
                    break
            if sub_len:
                hits.append((sub_len, name))

    if not hits:
        return (None, []) if with_candidates else None

    top = max(h[0] for h in hits)
    names = []
    for L, n in hits:
        if L == top and n not in names:
            names.append(n)

    if len(names) == 1:
        return (names[0], names) if with_candidates else names[0]
    # 有歧义：交给调用方提示用户补充说明
    return (None, names) if with_candidates else None


def course_names(courses):
    """去重后的课程名列表，用于提示"""
    out = []
    for c in courses:
        n = (c.get("course_name") or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def find_course_by_name(name, courses):
    for c in courses:
        if c.get("course_name") == name:
            return c
    return None


# ────────────────────────── 指令解析 ──────────────────────────

HOLIDAY_WORDS = ["放假", "假期", "休市", "休息", "放 假", "节假日"]
CANCEL_WORDS = ["停课", "停一次", "不上课", "取消课", "暂停", "不上"]
MAKEUP_WORDS = ["课表上课", "的课表", "上课", "补课", "调休", "补上", "上周", "上周一", "上周二"]
MOVE_WORDS = ["调到", "改到", "挪到", "移到", "移至", "换到", "调整到", "改为", "换成", "改期"]
ROOM_HINT = ["教室", "上课地点", "地点", "改在", "换教室"]

ROOM_TOKEN = re.compile(r"[A-Za-z]{1,3}\s?\d{2,4}|[\u4e00-\u9fa5]{2,6}(?:楼|馆|厅)\s?[A-Za-z]?\d{0,4}")


def _sanitize_room(raw):
    raw = raw.strip(" ，,。;；:：")
    raw = re.sub(r"^(教室|上课地点|地点|在|到|改在|换成|改为)", "", raw)
    raw = re.sub(r"(上课|教室)$", "", raw).strip()
    return raw


def _time_cond(seg, courses, today):
    """从一段文字中抽取时间条件：date / week / weekday / sections"""
    cond = {"date": None, "week": None, "weekday": None, "start": None, "end": None}
    ds = extract_dates(seg)
    if ds:
        cond["date"] = ds[0].strftime("%Y-%m-%d")
    rel = extract_relative_date(seg, today)
    if rel and not cond["date"]:
        cond["date"] = rel.strftime("%Y-%m-%d")
    w = extract_week(seg)
    if w:
        cond["week"] = w
    wd = extract_weekday(seg)
    if wd:
        cond["weekday"] = wd
    s, e = extract_sections(seg)
    cond["start"], cond["end"] = s, e
    return cond


def _cond_label(cond):
    parts = []
    if cond.get("date"):
        parts.append(cond["date"])
    else:
        if cond.get("week"):
            w = cond["week"]
            ws = sorted(w) if isinstance(w, (list, set, tuple)) else [w]
            parts.append(f"第{ws[0]}-{ws[-1]}周" if len(ws) > 1 else f"第{ws[0]}周")
        if cond.get("weekday"):
            parts.append(WEEKDAY_CN[cond["weekday"] - 1])
    if cond.get("start"):
        parts.append(f"{cond['start']}-{cond['end']}节")
    return " ".join(parts) or "全部时段"


def parse_command(text, courses, today=None, year=DEFAULT_YEAR):
    """
    把一句自然语言指令解析成调整操作列表。
    返回 {"ops": [...], "explain": [...], "needs_clarify": bool, "question": str}
    """
    global DEFAULT_YEAR
    DEFAULT_YEAR = year
    today = today or date.today()
    text = re.sub(r"\s+", " ", text).strip()
    ops, explains = [], []

    # 按句拆分，逐句解析
    sentences = [s for s in re.split(r"[。；;\n]|，(?=第|把|请将)", text) if s.strip()]
    if not sentences:
        sentences = [text]

    for sent in sentences:
        course, course_cands = match_course(sent, courses, with_candidates=True)
        dates = extract_dates(sent)
        ranges = extract_ranges(sent)
        rel = extract_relative_date(sent, today)
        sec_s, sec_e = extract_sections(sent)
        week = extract_week(sent)

        # ── 1. 放假 / 节假日停课 ──
        if any(w in sent for w in HOLIDAY_WORDS) and not course and not sec_s:
            days = []
            for s, e in ranges:
                cur = s
                while cur <= e:
                    days.append(cur.strftime("%Y-%m-%d"))
                    cur += timedelta(days=1)
            if len(dates) >= 2 and not ranges:
                s, e = dates[0], dates[-1]
                cur = s
                while cur <= e:
                    days.append(cur.strftime("%Y-%m-%d"))
                    cur += timedelta(days=1)
            if not days and dates:
                days = [d.strftime("%Y-%m-%d") for d in dates]
            if not days and rel:
                days = [rel.strftime("%Y-%m-%d")]
            if days:
                ops.append({
                    "kind": "holiday",
                    "label": f"{days[0]} ~ {days[-1]}（共{len(days)}天）"
                             if len(days) > 1 else f"{days[0]}",
                    "payload": {"dates": days},
                })
                explains.append(f"识别为【放假】，共 {len(days)} 天不上课")
                continue

        # ── 2. 调休补课：X日按Y日/星期Y课表上课 ──
        makeup_hit = ("课表上课" in sent) or ("按" in sent and "课表" in sent) or ("补课" in sent) \
            or bool(re.search(r"上(?:周|星期)[一二三四五六日天]的?课", sent))
        if makeup_hit:
            exec_date = dates[0] if dates else rel
            follow_weekday, follow_date = None, None
            # 优先：按 10月6日（星期二）课表上课 → 参考日期的星期
            if len(dates) >= 2:
                follow_date = dates[1].strftime("%Y-%m-%d")
                follow_weekday = dates[1].isoweekday()
            wd = None
            idx = sent.find("按")
            sub = sent[idx:] if idx != -1 else sent
            m = re.search(r"(?:星期|周)\s*([一二三四五六日天])", sub)
            if m:
                wd = CN2NUM[m.group(1)]
            if wd:
                follow_weekday = wd
            if exec_date and follow_weekday:
                ops.append({
                    "kind": "makeup",
                    "label": f"{exec_date.strftime('%Y-%m-%d')} 按{WEEKDAY_CN[follow_weekday - 1]}课表上课",
                    "payload": {
                        "date": exec_date.strftime("%Y-%m-%d"),
                        "follow_weekday": follow_weekday,
                        "follow_date": follow_date,
                    },
                })
                explains.append(
                    f"识别为【调休补课】：{exec_date.strftime('%Y-%m-%d')} 上 {WEEKDAY_CN[follow_weekday - 1]} 的课")
                continue

        # ── 3. 调课 / 换教室：A 调到 B ──
        mv_kw = None
        for kw in MOVE_WORDS:
            if kw in sent:
                mv_kw = kw
                break
        if mv_kw:
            left, right = sent.split(mv_kw, 1)
            left = re.sub(r"^(把|将|请|从)", "", left.strip())
            from_cond = _time_cond(left, courses, today)
            to_cond = _time_cond(right, courses, today)
            course = course or match_course(left, courses) or match_course(right, courses)
            room_raw = None
            for hint in ROOM_HINT:
                if hint in right:
                    room_raw = right.split(hint, 1)[-1]
            if room_raw is None:
                m = ROOM_TOKEN.search(right)
                # 只有右侧没有星期/节次信息时，才把右侧当成教室
                if m and not to_cond["weekday"] and not to_cond["start"] and not to_cond["date"]:
                    room_raw = m.group(0)
            if room_raw:
                room = _sanitize_room(room_raw)
                if room and course:
                    ops.append({
                        "kind": "room_change",
                        "label": f"「{course}」上课地点改为 {room}",
                        "payload": {**from_cond, "course_name": course, "room": room},
                    })
                    explains.append(f"识别为【更换教室】：{course} → {room}")
                    continue
            if not (from_cond["date"] or from_cond["weekday"]):
                # 左侧没写原时间（如"机器学习调到周五5-6节"），用课程在课表里的默认时段补齐。
                # 不能拿整句再解析一次，否则会把右侧的目标时间误当成原时间。
                slot = course_slot(course, courses) if course else {}
                for k, v in slot.items():
                    if not from_cond.get(k):
                        from_cond[k] = v
            if course and (from_cond["date"] or from_cond["weekday"]) and \
                    (to_cond["date"] or to_cond["weekday"] or to_cond["start"]):
                if not to_cond["start"] and from_cond["start"]:
                    to_cond["start"], to_cond["end"] = from_cond["start"], from_cond["end"]
                ops.append({
                    "kind": "move",
                    "label": f"「{course}」{_cond_label(from_cond)} → {_cond_label(to_cond)}",
                    # from 里带上课程名：否则"周三3-4节"会把同时段的其他课也一起标成已调走
                    "payload": {"course_name": course,
                                "from": {**from_cond, "course_name": course},
                                "to": to_cond},
                })
                explains.append(f"识别为【调课】：{course} 从 {_cond_label(from_cond)} 调到 {_cond_label(to_cond)}")
                continue

        # ── 4. 停课 ──
        if any(w in sent for w in CANCEL_WORDS):
            cond = _time_cond(sent, courses, today)
            if cond["date"] or cond["weekday"] or week:
                ops.append({
                    "kind": "cancel",
                    "label": f"{course or '全部课程'}（{_cond_label(cond)}）",
                    "payload": {**cond, "course_name": course},
                })
                explains.append(f"识别为【停课】：{course or '该时段全部课程'} @ {_cond_label(cond)}")
                continue

        # ── 5. 兜底：无法识别 ──
        if course_cands:
            explains.append("课程名有歧义，请写明是哪一门：" + " / ".join(course_cands))
        else:
            msg = f"没能理解：{sent[:30]}{'...' if len(sent) > 30 else ''}"
            if not course and any(w in sent for w in MOVE_WORDS + CANCEL_WORDS + HOLIDAY_WORDS):
                names = course_names(courses)
                msg += "。没在课表里找到对应课程，可用名称：" + "、".join(names[:8]) + \
                       ("…" if len(names) > 8 else "")
            explains.append(msg)

    return {
        "ops": ops,
        "explain": explains,
        "needs_clarify": len(ops) == 0,
        "question": None if ops else "没看懂这条指令，试试：'10月1日到10月7日放假'、'9月20日按10月6日课表上课'、"
                                      "'第5周周三高等运筹学停课'、'把周三3-4节的机器学习调到周五5-6节'",
    }


# ────────────────────────── 通知全文解析（OCR 文本） ──────────────────────────


def parse_notice(text, year=DEFAULT_YEAR):
    """从放假/调休通知全文中抽取规则（OCR 文本专用）"""
    global DEFAULT_YEAR
    DEFAULT_YEAR = year
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    ops, explains = [], []

    # 放假区间
    lines = re.split(r"[。；\n]", text)
    for i, line in enumerate(lines):
        if not any(w in line for w in ["放假", "假期", "休息"]):
            continue
        # 标题行本身没有日期时，看下一行
        scope = line if DATE_RE.search(line) or RANGE_RE.search(line) else (
            lines[i + 1] if i + 1 < len(lines) else "")
        days = []
        for s, e in extract_ranges(scope):
            cur = s
            while cur <= e:
                days.append(cur.strftime("%Y-%m-%d"))
                cur += timedelta(days=1)
        if not days:
            ds = extract_dates(scope)
            if len(ds) >= 2:
                cur, e = ds[0], ds[-1]
                while cur <= e:
                    days.append(cur.strftime("%Y-%m-%d"))
                    cur += timedelta(days=1)
            elif ds:
                days = [d.strftime("%Y-%m-%d") for d in ds]
        if days:
            ops.append({
                "kind": "holiday",
                "label": f"{days[0]} ~ {days[-1]}（共{len(days)}天）"
                         if len(days) > 1 else f"{days[0]}",
                "payload": {"dates": days},
            })
            explains.append(f"从通知中识别到【放假】{len(days)} 天")
            break

    # 调休补课：含"课表上课"的每一条
    for line in re.split(r"[。；;\n]|(?=\d\s*[.、])", text):
        if "课表上课" not in line and not re.search(r"上(?:周|星期)[一二三四五六日天]的?课", line):
            continue
        ds = extract_dates(line)
        if not ds:
            continue
        exec_date = ds[0]
        follow_weekday = None
        follow_date = None
        if len(ds) >= 2:
            follow_date = ds[1].strftime("%Y-%m-%d")
            follow_weekday = ds[1].isoweekday()
        idx = line.find("按")
        sub = line[idx:] if idx != -1 else line
        m = re.search(r"(?:星期|周)\s*([一二三四五六日天])", sub)
        if m:
            follow_weekday = CN2NUM[m.group(1)]
        if follow_weekday:
            ops.append({
                "kind": "makeup",
                "label": f"{exec_date.strftime('%Y-%m-%d')} 按{WEEKDAY_CN[follow_weekday - 1]}课表上课",
                "payload": {
                    "date": exec_date.strftime("%Y-%m-%d"),
                    "follow_weekday": follow_weekday,
                    "follow_date": follow_date,
                },
            })
            explains.append(
                f"从通知中识别到【调休补课】：{exec_date.strftime('%m月%d日')} 上 {WEEKDAY_CN[follow_weekday - 1]} 的课")

    return {"ops": ops, "explain": explains, "needs_clarify": len(ops) == 0, "question": None if ops else
            "这段文本里没找到放假或补课安排"}


def parse_text_auto(text, courses, year=DEFAULT_YEAR):
    """自动判断：通知全文 → parse_notice；短指令 → parse_command"""
    if len(text) > 60 or "通知" in text[:40] or "课表上课" in text:
        r = parse_notice(text, year)
        if r["ops"]:
            return r
    return parse_command(text, courses, year=year)


# ────────────────────────── 课表计算 ──────────────────────────


def _cond_match(cond, d, week, weekday, start, end, course_name):
    if cond.get("date"):
        if cond["date"] != d.strftime("%Y-%m-%d"):
            return False
    else:
        if cond.get("week") is not None:
            w = cond["week"]
            weeks = set(w) if isinstance(w, (list, set, tuple)) else {w}
            if week not in weeks:
                return False
        if cond.get("weekday") is not None and cond["weekday"] != weekday:
            return False
    if cond.get("start") and cond.get("end"):
        if not (start <= cond["end"] and end >= cond["start"]):
            return False
    if cond.get("course_name"):
        if cond["course_name"] not in course_name and course_name not in cond["course_name"]:
            return False
    return True


def render_date(d, courses, adjustments, term_start):
    """计算某一天实际上的课"""
    d = to_date(d)
    ds = d.strftime("%Y-%m-%d")
    week = week_of(d, term_start)
    iso = d.isoweekday()
    result = {"date": ds, "weekday": iso, "week": week, "flags": [], "items": []}

    # 1) 放假 → 整天没课
    for a in adjustments:
        if a.get("kind") == "holiday" and ds in a.get("payload", {}).get("dates", []):
            result["flags"].append({"type": "holiday", "label": "放假"})
            return result

    # 2) 调休补课 → 换一张课表来上
    eff_weekday = iso
    for a in adjustments:
        if a.get("kind") == "makeup" and a.get("payload", {}).get("date") == ds:
            fw = a["payload"].get("follow_weekday")
            if fw:
                eff_weekday = fw
                result["flags"].append({"type": "makeup", "label": f"按{WEEKDAY_CN[fw - 1]}课表上课"})
    result["eff_weekday"] = eff_weekday

    if week is None:
        result["flags"].append({"type": "out", "label": "非学期周"})
        return result

    # 3) 基础课表
    for c in courses:
        if c.get("weekday") == eff_weekday and week in parse_week_range(c.get("week_range", "")):
            result["items"].append({
                "course_name": c["course_name"],
                "start": c["start_section"],
                "end": c["end_section"],
                "classroom": c.get("classroom", ""),
                "week_range": c.get("week_range", ""),
                "status": "normal",
                "tags": [],
            })

    # 4) 调课：从原时段移除
    for a in adjustments:
        if a.get("kind") != "move":
            continue
        fc = a["payload"].get("from", {})
        # 兼容早期保存的规则：from 里没写课程名时，用规则本身的课程名兜底
        _name = a["payload"].get("course_name")
        if _name and not fc.get("course_name"):
            fc = {**fc, "course_name": _name}
        for it in result["items"]:
            if _cond_match(fc, d, week, eff_weekday, it["start"], it["end"], it["course_name"]):
                it["status"] = "moved_out"
                it["tags"].append("已调走")

    # 5) 停课
    for a in adjustments:
        if a.get("kind") != "cancel":
            continue
        cond = a["payload"]
        for it in result["items"]:
            if it["status"] == "moved_out":
                continue
            if _cond_match(cond, d, week, eff_weekday, it["start"], it["end"], it["course_name"]):
                it["status"] = "cancelled"
                it["tags"].append("停课")

    # 6) 调课：在新时段加入
    for a in adjustments:
        if a.get("kind") != "move":
            continue
        p = a["payload"]
        tc = p.get("to", {})
        name = p.get("course_name")
        if not name:
            continue
        src = find_course_by_name(name, courses) or {}
        s = tc.get("start") or src.get("start_section")
        e = tc.get("end") or src.get("end_section")
        if not s:
            continue
        # 目标是否落在今天（未指定周次时，只在课程原本的上课周内生效）
        hit = _cond_match({k: v for k, v in tc.items() if k in ("date", "week", "weekday")},
                          d, week, eff_weekday, s, e, name)
        if hit and tc.get("week") is None and src.get("week_range"):
            hit = week in parse_week_range(src["week_range"])
        if hit:
            result["items"].append({
                "course_name": name,
                "start": s,
                "end": e,
                "classroom": tc.get("room") or src.get("classroom", ""),
                "week_range": src.get("week_range", ""),
                "status": "moved_in",
                "tags": ["调入"],
            })

    # 7) 换教室
    for a in adjustments:
        if a.get("kind") != "room_change":
            continue
        cond = a["payload"]
        for it in result["items"]:
            if _cond_match(cond, d, week, eff_weekday, it["start"], it["end"], it["course_name"]):
                it["classroom"] = cond.get("room", it["classroom"])
                it["tags"].append("换教室")

    result["items"].sort(key=lambda x: x["start"])
    return result


def render_week(week, courses, adjustments, term_start):
    """计算第 N 周整周的实际课表"""
    term_start = to_date(term_start)
    monday = week_monday(term_start, week)
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append(render_date(d, courses, adjustments, term_start))
    return {"week": week, "monday": monday.strftime("%Y-%m-%d"), "days": days}
