#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课表转规范化 CSV 工具（标准 .docx 版）
========================================
把 Word/WPS 标准 .docx / .xlsx / .ics / .txt 课表解析成标准课程记录。

目标格式（每行 = 一门课的一次排课）：
    course_name,weekday,start_section,end_section,week_range,classroom
    机器学习与数据分析班（1班）,3,3,4,2-9,将军路D2312

课程单元格采用“方括号条目”写法：
    课程名[周次]教师[教室]
    例：机器学习与数据分析班（1班）[2-9周]高园园[将军路D2312]
    同一格内可堆叠多条，用空行分隔；教师名不写入记录。

额外能力：
    parse_* 函数返回 (records, section_times)
    section_times 为从文件中解析出的节次起止时间列表 [{"start":"08:00","end":"08:50"}, ...]
    若文件不含时间信息则返回 None，由前端允许用户手动设置。

用法：
    python timetable_to_csv.py 课表.docx
    python timetable_to_csv.py 课表.docx -o D:\\输出目录
不带参数直接运行时，会提示你粘贴/拖入文件路径。

依赖：
    pip install python-docx openpyxl
"""

import argparse
import csv
import re
import sys
from pathlib import Path

# ======================================================================
# 条目解析：课程名[周次]教师[教室]
# ======================================================================
ENTRY_RE = re.compile(
    r"([^\[\]【】]+?)[\[【](\d{1,2}(?:-\d{1,2})?)周?[\]】]\s*[^\[\]【】]*?[\[【]([^\[\]【】]+?)[\]】]"
)

WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
FIELDS = ["course_name", "weekday", "start_section", "end_section", "week_range", "classroom"]

# 节次时间正则：匹配 "8:00-8:50" / "08:00~08:50" / "8：00-8：50" 等
TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[:：]\s*(\d{1,2})\s*[-—–~～至到]\s*(\d{1,2})\s*[:：]\s*(\d{1,2})"
)
# 单个节次号：匹配 "1" / "第1节" / "第一节" 等
SECTION_NUM_RE = re.compile(r"第?\s*(\d{1,2})\s*节?")


def clean(text):
    return " ".join("".join(text).split())


def parse_entries(text):
    return [(m.group(1).strip(), m.group(2), m.group(3).strip())
            for m in ENTRY_RE.finditer(text)]


def _parse_time_range(text):
    """从一段文字中提取起止时间，返回 (start_str, end_str) 或 None"""
    m = TIME_RANGE_RE.search(text or "")
    if m:
        s = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
        e = f"{int(m.group(3)):02d}:{int(m.group(4)):02d}"
        return s, e
    return None


def _extract_section_times(first_col_texts):
    """
    从节次列的文本列表中提取每节的起止时间。
    first_col_texts: 节次列每一行的原始文本
    返回: [{"start":"08:00","end":"08:50"}, ...] 或 None
    """
    times = []
    for txt in first_col_texts:
        t = _parse_time_range(txt)
        if t:
            times.append({"start": t[0], "end": t[1]})
    return times if times else None


# ======================================================================
# docx 网格解析（原始 XML，正确处理合并单元格）
# ======================================================================
def _vmerge(tc, qn):
    tcPr = tc.find(qn("w:tcPr"))
    if tcPr is None:
        return None
    v = tcPr.find(qn("w:vMerge"))
    if v is None:
        return None
    return v.get(qn("w:val")) or "continue"


def parse_docx_bytes(raw: bytes):
    """从内存中的 .docx 字节解析（供 Web 上传调用），失败抛异常而非退出进程。"""
    import io as _io
    return _parse_docx(_io.BytesIO(raw))


def parse_docx(path: Path):
    return _parse_docx(str(path))


def _parse_docx(source):
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(source) if not hasattr(source, "read") else source)
    if not doc.tables:
        raise ValueError("文档中没有表格，请确认是教务系统导出的课表 docx。")
    tbl = doc.tables[0]._tbl
    trs = list(tbl.iterchildren(qn("w:tr")))

    rows = []
    for tr in trs:
        cells = []
        for tc in tr.iterchildren(qn("w:tc")):
            txt = clean("".join(tc.itertext()))
            cells.append((_vmerge(tc, qn), parse_entries(txt), txt))
        rows.append(cells)

    # 表头：含多个“星期X”的行，建立 物理列 -> 星期数字
    hdr = {}
    for cells in rows:
        local, hits = {}, 0
        for c, (vm, ent, txt) in enumerate(cells):
            m = re.search(r"星期([一二三四五六日天])", txt)
            if m:
                local[c] = WEEKDAY_MAP[m.group(1)]
                hits += 1
        if hits >= 2:
            hdr = local
            break

    if not hdr:
        raise ValueError("未识别到课表表头（缺少“星期一/星期二…”），请确认上传的是周课表 docx。")

    def section_of(r):
        m = re.search(r"(\d+)", rows[r][0][2])
        return int(m.group(1)) if m else r

    anchors = []
    for r, cells in enumerate(rows):
        for c, (vm, ent, _) in enumerate(cells):
            if ent and vm == "restart":
                end = r
                rr = r + 1
                while rr < len(rows) and rows[rr][c][0] == "continue":
                    end = rr
                    rr += 1
                anchors.append({"start": r, "end": end, "col": c, "entries": ent})
            elif ent and vm is None:
                anchors.append({"start": r, "end": r, "col": c, "entries": ent})

    records = []
    for a in anchors:
        wd = hdr.get(a["col"])
        s, e = section_of(a["start"]), section_of(a["end"])
        for name, week, room in a["entries"]:
            records.append({
                "course_name": name, "weekday": wd, "start_section": s,
                "end_section": e, "week_range": week, "classroom": room,
            })

    records = [r for r in records if r["weekday"] is not None]

    seen, uniq = set(), []
    for x in records:
        k = (x["course_name"], x["weekday"], x["start_section"], x["end_section"],
             x["week_range"], x["classroom"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    uniq.sort(key=lambda x: (x["weekday"], x["start_section"], x["week_range"], x["course_name"]))

    # ── 提取节次时间：扫描所有非表头列，找到包含时间范围（如 08:00-08:50）的列 ──
    n_cols = max((len(r) for r in rows), default=0)
    time_col = None
    best_count = 0
    for c in range(n_cols):
        if c in hdr:
            continue
        count = sum(1 for r in rows if c < len(r) and TIME_RANGE_RE.search(r[c][2] or ""))
        if count > best_count:
            best_count = count
            time_col = c

    section_times = None
    if time_col is not None:
        time_col_texts = [r[time_col][2] for r in rows if time_col < len(r)]
        section_times = _extract_section_times(time_col_texts)

    return uniq, section_times


# ======================================================================
# xlsx 解析
# ======================================================================
def parse_xlsx_bytes(raw: bytes):
    """解析 .xlsx 课表，返回 (records, section_times)"""
    import io as _io
    from openpyxl import load_workbook

    wb = load_workbook(_io.BytesIO(raw), data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        rows.append([clean(str(c)) if c is not None else "" for c in row])

    return _parse_grid(rows)


# ======================================================================
# 通用网格解析（docx/xlsx 共用逻辑）
# ======================================================================
def _parse_grid(rows):
    """
    rows: 二维列表（字符串），第一行可能是表头。
    返回 (records, section_times)
    """
    if not rows:
        raise ValueError("文件为空")

    # 找表头行（含多个星期X）
    hdr = {}
    hdr_row_idx = -1
    for ri, cells in enumerate(rows):
        local, hits = {}, 0
        for c, txt in enumerate(cells):
            m = re.search(r"星期([一二三四五六日天])", txt or "")
            if m:
                local[c] = WEEKDAY_MAP[m.group(1)]
                hits += 1
        if hits >= 2:
            hdr = local
            hdr_row_idx = ri
            break

    if not hdr:
        raise ValueError("未识别到课表表头（缺少“星期一/星期二…”）。")

    # 节次号列：在非表头列中找匹配"第N节/数字"最多的列
    # 时间列：在非表头列中找匹配时间范围（08:00-08:50）最多的列
    # 两者可能不同（如 docx/xlsx 中第0列是"上午1"，第1列才是时间）
    n_cols = max((len(r) for r in rows), default=0)
    section_num_col = None
    time_col = None
    best_sec_count = 0
    best_time_count = 0
    for c in range(n_cols):
        if c in hdr:
            continue
        sec_count = 0
        time_count = 0
        for r in range(hdr_row_idx + 1, len(rows)):
            if c < len(rows[r]):
                txt = rows[r][c] or ""
                if SECTION_NUM_RE.search(txt):
                    sec_count += 1
                if TIME_RANGE_RE.search(txt):
                    time_count += 1
        if sec_count > best_sec_count:
            best_sec_count = sec_count
            section_num_col = c
        if time_count > best_time_count:
            best_time_count = time_count
            time_col = c

    # 提取节次时间
    first_col_texts = []
    if time_col is not None:
        for r in range(hdr_row_idx + 1, len(rows)):
            if r < len(rows) and time_col < len(rows[r]):
                first_col_texts.append(rows[r][time_col])
    section_times = _extract_section_times(first_col_texts)

    # 节次号列：优先用专门的节次号列，没有则退回到时间列，再没有用 0
    sec_col = section_num_col if section_num_col is not None else (time_col if time_col is not None else 0)

    records = []
    for r in range(hdr_row_idx + 1, len(rows)):
        cells = rows[r]
        # 节次号
        sec_text = cells[sec_col] if sec_col < len(cells) else ""
        sec_match = SECTION_NUM_RE.search(sec_text)
        if not sec_match:
            continue
        sec_num = int(sec_match.group(1))
        for c, wd in hdr.items():
            if c >= len(cells):
                continue
            txt = cells[c]
            if not txt or not txt.strip():
                continue
            entries = parse_entries(txt)
            for name, week, room in entries:
                records.append({
                    "course_name": name, "weekday": wd,
                    "start_section": sec_num, "end_section": sec_num,
                    "week_range": week, "classroom": room,
                })

    seen, uniq = set(), []
    for x in records:
        k = (x["course_name"], x["weekday"], x["start_section"], x["end_section"],
             x["week_range"], x["classroom"])
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    uniq.sort(key=lambda x: (x["weekday"], x["start_section"], x["week_range"], x["course_name"]))
    return uniq, section_times


# ======================================================================
# .ics 解析（iCalendar 日历格式）
# ======================================================================
def parse_ics_bytes(raw: bytes):
    """解析 .ics 课表，返回 (records, section_times)"""
    text = raw.decode("utf-8", errors="replace")
    # 兼容 \r\n 折行
    lines = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)

    events = []
    cur = {}
    in_event = False
    for line in lines:
        if line == "BEGIN:VEVENT":
            in_event = True
            cur = {}
        elif line == "END:VEVENT":
            in_event = False
            if cur:
                events.append(cur)
            cur = {}
        elif in_event and ":" in line:
            key, _, val = line.partition(":")
            key = key.split(";")[0].upper()
            cur[key] = val

    if not events:
        raise ValueError("ICS 文件中没有课程事件（VEVENT）。")

    from datetime import datetime as _dt
    records = []
    for ev in events:
        name = ev.get("SUMMARY", "").strip()
        if not name:
            continue
        dtstart = ev.get("DTSTART", "")
        dtend = ev.get("DTEND", "")
        # 解析日期时间
        def parse_dt(s):
            s = s.strip()
            for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
                try:
                    return _dt.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        ds = parse_dt(dtstart)
        de = parse_dt(dtend)
        if not ds:
            continue
        weekday = ds.isoweekday()
        # 节次：从时间推算（假设 8:00 为第1节，每节50分钟+10分钟课间）
        start_min = ds.hour * 60 + ds.minute
        sec_start = max(1, (start_min - 8 * 60) // 60 + 1)
        sec_end = sec_start
        if de:
            end_min = de.hour * 60 + de.minute
            dur = end_min - start_min
            sec_end = sec_start + max(0, dur // 55)
        sec_end = min(sec_end, 12)
        # 教室
        room = ev.get("LOCATION", "").strip()
        # 周次：简化处理，从 RRULE 或日期范围推断（暂设为全学期 1-20）
        week_range = "1-20"
        rrule = ev.get("RRULE", "")
        if "COUNT" in rrule:
            m = re.search(r"COUNT=(\d+)", rrule)
            if m:
                weeks = int(m.group(1))
                week_range = f"1-{weeks}"

        records.append({
            "course_name": name, "weekday": weekday,
            "start_section": sec_start, "end_section": sec_end,
            "week_range": week_range, "classroom": room,
        })

    if not records:
        raise ValueError("ICS 文件中没有可解析的课程。")
    return records, None


# ======================================================================
# .txt 解析（纯文本课表）
# ======================================================================
def parse_txt_bytes(raw: bytes):
    """解析纯文本课表，返回 (records, section_times)

    支持两种格式：
    1. 表格风格（用 | 或制表符分隔，第一行表头含星期）
    2. 逐行课程：课程名 星期X 第N-M节 [周次] [教室]
    """
    text = raw.decode("utf-8-sig", errors="replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("文本为空")

    # 尝试表格风格
    has_pipe = any("|" in l for l in lines)
    has_tab = any("\t" in l for l in lines)
    if has_pipe or has_tab:
        sep = "|" if has_pipe else "\t"
        rows = [[c.strip() for c in l.split(sep) if c is not None] for l in lines]
        return _parse_grid(rows)

    # 逐行课程风格
    records = []
    COURSE_LINE_RE = re.compile(
        r"(.+?)\s+星期([一二三四五六日天])\s+第?\s*(\d{1,2})\s*(?:[-~～至到]\s*(\d{1,2}))?\s*节"
        r"(?:\s*\[(\d+(?:-\d+)?)\s*周?\])?(?:\s*[\[【]?([^\[\]【】]+?)[\]】]?)?$"
    )
    for line in lines:
        m = COURSE_LINE_RE.search(line)
        if not m:
            continue
        name = m.group(1).strip()
        wd = WEEKDAY_MAP[m.group(2)]
        s = int(m.group(3))
        e = int(m.group(4)) if m.group(4) else s
        week = m.group(5) or "1-20"
        room = (m.group(6) or "").strip()
        records.append({
            "course_name": name, "weekday": wd,
            "start_section": s, "end_section": e,
            "week_range": week, "classroom": room,
        })

    if not records:
        raise ValueError("文本中没有识别到课程行。格式示例：机器学习 周三 第3-4节 [2-9周] [D2312]")
    return records, None


# ======================================================================
# 统一入口：根据扩展名分发
# ======================================================================
def parse_file_bytes(raw: bytes, filename: str):
    """根据文件扩展名选择解析器，返回 (records, section_times)"""
    name = (filename or "").lower()
    if name.endswith(".docx") or raw[:2] == b"PK":
        return parse_docx_bytes(raw)
    if name.endswith((".xlsx", ".xls")):
        return parse_xlsx_bytes(raw)
    if name.endswith(".ics"):
        return parse_ics_bytes(raw)
    if name.endswith(".txt"):
        return parse_txt_bytes(raw)
    if name.endswith(".csv"):
        # CSV 走单独的字段解析（在 app.py 中处理）
        raise ValueError("CSV 格式请使用 CSV 分支解析")
    raise ValueError(f"不支持的文件格式：{filename}")


def write_csv(records, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(records)
    print(f"  [已保存 {len(records)} 条] {out_path}")


def ask_input():
    print("请把课表文件拖进窗口（或粘贴完整路径）后回车：")
    return input("> ").strip().strip('"').strip("'").strip()


def main():
    ap = argparse.ArgumentParser(description="课表转规范化 CSV")
    ap.add_argument("input", nargs="?", help="课表文件(.docx/.xlsx/.ics/.txt)")
    ap.add_argument("-o", "--out", default=None, help="输出目录（默认同目录 文件名_csv）")
    args = ap.parse_args()

    src = Path(str(args.input or ask_input()).strip().strip('"').strip("'").strip())
    if not src.exists():
        sys.exit(f"找不到文件：{src}\n请确认路径是否正确、是否带引号。")

    out_dir = Path(args.out) if args.out else src.parent / (src.stem + "_csv")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        records, section_times = parse_file_bytes(src.read_bytes(), src.name)
    except ValueError as e:
        sys.exit(f"解析失败：{e}")
    if not records:
        sys.exit("没有解析到任何课程，请确认课表格式。")
    write_csv(records, out_dir / f"{src.stem}.csv")
    if section_times:
        print(f"\n解析到节次时间：{len(section_times)} 节")
    print(f"\n完成！输出目录：{out_dir}")


if __name__ == "__main__":
    main()
