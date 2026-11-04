# -*- coding: utf-8 -*-
"""
timetable_view.py
模块4：动态课表渲染 & 放假/调休规则应用（Streamlit Web 原型）
----------------------------------------------------------------
功能：读取成员1输出的 courses.json 与成员3输出的 adjust_rule.json，
      把放假/调休规则作用到每一天，渲染“被调整后的课表”。
      支持上传课表 JSON、上传调休通知截图（OCR 自动解析规则）。
运行：streamlit run timetable_view.py
"""

import io
import json
import re
import datetime as dt
from pathlib import Path

import streamlit as st
import pandas as pd
from dateutil import parser as date_parser

# ----------------------------------------------------------------------
# 0. 常量
# ----------------------------------------------------------------------
WEEKDAY_CN = {1: "星期一", 2: "星期二", 3: "星期三",
              4: "星期四", 5: "星期五", 6: "星期六", 7: "星期日"}
CN_TO_WEEKDAY = {v: k for k, v in WEEKDAY_CN.items()}
# 兼容各种星期简称
CN_TO_WEEKDAY.update({
    "周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7,
    "周天": 7, "星期日": 7, "星期天": 7, "礼拜一": 1, "礼拜二": 2, "礼拜三": 3,
    "礼拜四": 4, "礼拜五": 5, "礼拜六": 6, "礼拜天": 7, "礼拜日": 7,
})

MAX_SECTION = 11          # 节次渲染上限（数据最大到11节）
DAYS = [1, 2, 3, 4, 5, 6, 7]  # 周一~周日（含周日以展示周日调休）

COLOR_POOL = ["#dbeafe", "#dcfce7", "#fef9c3", "#fce7f3",
              "#ede9fe", "#ffedd5", "#cffafe", "#fee2e2"]

NOTICE_YEAR = 2026
WEEK_LIST = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
             "周一", "周二", "周三", "周四", "周五", "周六", "周日"]


# ----------------------------------------------------------------------
# 1. 数据加载与解析（纯函数，便于测试）
# ----------------------------------------------------------------------
def load_courses(path: str) -> list:
    """读取成员1输出的 courses.json（纯课程列表）。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):            # 兼容 {"courses": [...]} 的包裹格式
        data = data.get("courses", [])
    return data


def parse_week_range(wr: str) -> set:
    """把 '2-9' / '17-17' / '2,4,6' 解析成周次集合。"""
    weeks = set()
    for part in str(wr).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            weeks.update(range(int(a), int(b) + 1))
        else:
            weeks.add(int(part))
    return weeks


def load_rules(path: str) -> dict:
    """读取成员3输出的 adjust_rule.json。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------
# 2. 核心规则逻辑（纯函数）
# ----------------------------------------------------------------------
def week_dates(semester_start: dt.date, week_no: int) -> dict:
    """返回 {weekday: date}，覆盖周一~周日。"""
    monday = semester_start + dt.timedelta(days=(week_no - 1) * 7)
    return {d: monday + dt.timedelta(days=d - 1) for d in range(1, 8)}


def day_status(date: dt.date, rules: dict, apply_rules: bool) -> dict:
    """
    计算某一天的状态。
    返回 {"status": "normal"|"holiday"|"swap",
          "display_weekday": 实际展示星期几,
          "note": 说明文字}
    """
    if not apply_rules:
        wd = date.isoweekday()
        return {"status": "normal", "display_weekday": wd, "note": ""}

    ds = date.isoformat()
    holidays = set(rules.get("holiday_dates", []))
    swaps = rules.get("workday_adjust", {}) or {}

    if ds in holidays:
        return {"status": "holiday", "display_weekday": date.isoweekday(),
                "note": "放假"}
    if ds in swaps:
        target = CN_TO_WEEKDAY.get(swaps[ds])
        if target:
            return {"status": "swap", "display_weekday": target,
                    "note": f"调休 → 按{WEEKDAY_CN[target]}上课"}
    return {"status": "normal", "display_weekday": date.isoweekday(), "note": ""}


def courses_of_weekday(courses: list, weekday: int, week_no: int) -> list:
    """某星期几、在某周有效的全部课程。"""
    out = []
    for c in courses:
        if int(c["weekday"]) != weekday:
            continue
        if week_no in parse_week_range(c.get("week_range", "")):
            out.append(c)
    return out


def build_grid(courses, rules, semester_start, week_no, apply_rules=True) -> dict:
    """
    生成某周一整周的课表网格。
    返回 {weekday: {"date":..., "status":..., "display_weekday":...,
                    "note":..., "cells": {start_section: course}}}
    """
    dates = week_dates(semester_start, week_no)
    grid = {}
    for wd in DAYS:
        date = dates[wd]
        stt = day_status(date, rules, apply_rules)
        cells = {}
        if stt["status"] != "holiday":          # 放假当天所有课程隐藏
            for c in courses_of_weekday(courses, stt["display_weekday"], week_no):
                cells[int(c["start_section"])] = c
        grid[wd] = {"date": date, "status": stt["status"],
                    "display_weekday": stt["display_weekday"],
                    "note": stt["note"], "cells": cells}
    return grid


# ----------------------------------------------------------------------
# 3. 通知文本 → 规则解析（复用成员3 李涵睿 的要素抽取逻辑，去掉 jieba 依赖）
# ----------------------------------------------------------------------
def _clean_text(raw: str) -> str:
    text = raw.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[※★■●◆🌹！?？]", "", text)
    return text.strip()


def _parse_one_date(date_str: str):
    try:
        return date_parser.parse(date_str, fuzzy=True,
                                 default=dt.datetime(NOTICE_YEAR, 1, 1))
    except Exception:
        return None


def _expand_date_range(text: str) -> list:
    """解析日期区间「A至B」，展开全部放假日期。"""
    holiday_days = []
    pat = re.compile(r"(\d{1,2}月\d{1,2}日[^至到‑—]{0,10})[至到‑—]([^，。；\s]{1,20})")
    for start_str, end_str in pat.findall(text):
        ds = _parse_one_date(start_str)
        de = _parse_one_date(end_str)
        if ds is None or de is None:
            continue
        cur = ds
        while cur <= de:
            holiday_days.append(cur.strftime("%Y-%m-%d"))
            cur += dt.timedelta(days=1)
    return holiday_days


def parse_rules_from_text(text: str) -> dict:
    """
    从通知文本中抽取放假/调休规则（成员3 逻辑的集成版）。
      - 放假区间：'9月25日至10月7日'
      - 调休补课：含'课表上课'的行，如 '9月20日（星期日）：按10月6日（星期二）课表上课'
    """
    cleaned = _clean_text(text)

    # 放假：区间 + 单日放假
    range_holiday = _expand_date_range(cleaned)
    single_holiday = []
    # 只提取 "X月X日...放假" 模式中的日期，避免把调休日算进放假
    for m in re.finditer(r"(\d{1,2}月\d{1,2}日)[^。\n]{0,8}?放假", cleaned):
        d = _parse_one_date(m.group(1))
        if d:
            single_holiday.append(d.strftime("%Y-%m-%d"))
    holiday_total = sorted(list(set(range_holiday + single_holiday)))

    # 调休：匹配 "日期...按星期X课表上课" 或 "日期...上星期X的课"
    adjust_map = {}
    swap_pat = re.compile(
        r"(\d{1,2}月\d{1,2}日)[^。\n]*?"
        r"(?:按|上)[^。\n]{0,15}?"
        r"(星期[一二三四五六日]|周[一二三四五六日天]|礼拜[一二三四五六日天])"
        r"[^。\n]{0,6}?(?:课表上课|上课|的课)")
    for m in swap_pat.finditer(text):
        work_date = _parse_one_date(m.group(1))
        if work_date:
            adjust_map[work_date.strftime("%Y-%m-%d")] = m.group(2)

    # 调休上班日从放假列表中移除
    work_day_set = set(adjust_map.keys())
    holiday_total = [d for d in holiday_total if d not in work_day_set]

    return {
        "type": "holiday_notice",
        "holiday_dates": holiday_total,
        "workday_adjust": adjust_map,
        "course_adjust": [],
    }


# ----------------------------------------------------------------------
# 4. OCR（尝试 PaddleOCR，失败则回退到手动粘贴文本）
# ----------------------------------------------------------------------
def ocr_image(image_bytes: bytes) -> str:
    """
    对上传的截图做 OCR。优先用 PaddleOCR；不可用时返回空串，
    由 UI 提示用户手动粘贴文本。
    """
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        return ""

    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)[:, :, ::-1]        # RGB -> BGR

    try:
        # paddleocr 3.x：初始化参数与返回结构都变了；enable_mkldnn=False 规避 oneDNN 内核 bug
        ocr = PaddleOCR(lang="ch",
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                        enable_mkldnn=False,
                        text_detection_model_name="PP-OCRv5_mobile_det",
                        text_recognition_model_name="PP-OCRv5_mobile_rec")
        lines = []
        for res in ocr.predict(arr):
            texts, scores = res["rec_texts"], res["rec_scores"]
            try:
                boxes = res["rec_boxes"]
            except Exception:
                boxes = None
            items = sorted(zip(range(len(texts)), texts, scores),
                           key=lambda it: float(boxes[it[0]][1]) if boxes is not None else it[0])
            lines = [t for _, t, _ in items]
        return "\n".join(lines)
    except TypeError:
        # paddleocr 2.x：旧接口
        try:
            ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            result = ocr.ocr(arr, cls=True)
            lines = []
            if result and result[0]:
                items = sorted(result[0], key=lambda x: x[0][0][1])
                for line in items:
                    lines.append(line[1][0])
            return "\n".join(lines)
        except Exception as e:
            st.warning(f"OCR 识别失败：{e}，请手动粘贴文本。")
            return ""
    except Exception as e:
        st.warning(f"OCR 识别失败：{e}，请手动粘贴文本。")
        return ""


# ----------------------------------------------------------------------
# 5. 端到端测试（正常周 / 单纯放假 / 放假+调休组合 / 规则开关）
# ----------------------------------------------------------------------
FIXTURE_COURSES = [
    {"course_name": "高等数学", "weekday": 1, "start_section": 1, "end_section": 2,
     "week_range": "1-16", "classroom": "A101"},
    {"course_name": "大学英语", "weekday": 3, "start_section": 3, "end_section": 4,
     "week_range": "1-16", "classroom": "B203"},
    {"course_name": "体育",     "weekday": 6, "start_section": 7, "end_section": 8,
     "week_range": "1-16", "classroom": "体育场"},
]
FIXTURE_RULES = {
    "holiday_dates": ["2026-09-25", "2026-09-26", "2026-09-27",
                      "2026-09-28", "2026-09-29", "2026-09-30",
                      "2026-10-01", "2026-10-02", "2026-10-03",
                      "2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07"],
    "workday_adjust": {"2026-09-20": "星期二", "2026-10-10": "星期三"},
    "course_adjust": [],
}
START = dt.date(2026, 9, 7)  # 2026-09-07 为周一


def run_tests():
    results = []

    # 用例1：正常周（第1周 9/7-9/13，无放假调休）→ 课表原样
    g = build_grid(FIXTURE_COURSES, FIXTURE_RULES, START, week_no=1)
    ok = (len(g[1]["cells"]) == 1 and g[1]["status"] == "normal"
          and len(g[3]["cells"]) == 1 and len(g[6]["cells"]) == 1
          and all(g[d]["status"] == "normal" for d in DAYS))
    results.append(("用例1 正常周：第1周课程完整显示、无隐藏",
                    ok, "周一高数/周三英语/周六体育均显示，状态全为 normal"))

    # 用例2：单纯放假（第3周 9/21-9/27，9/25周五、9/26周六 放假）
    g = build_grid(FIXTURE_COURSES, FIXTURE_RULES, START, week_no=3)
    ok = (g[5]["status"] == "holiday" and g[6]["status"] == "holiday"
          and len(g[5]["cells"]) == 0 and len(g[6]["cells"]) == 0
          and g[4]["status"] == "normal" and len(g[1]["cells"]) == 1)
    results.append(("用例2 单纯放假：第3周周五/周六显示『放假』且课程隐藏",
                    ok, "9/25(五)、9/26(六) holiday，周一/周四课程不受影响"))

    # 用例3：放假+调休组合（第5周 10/5-10/11，
    #        周一~周三 10/5-10/7 放假，周六 10/10 调休上星期三的课）
    g = build_grid(FIXTURE_COURSES, FIXTURE_RULES, START, week_no=5)
    ok = (all(g[d]["status"] == "holiday" for d in [1, 2, 3])
          and g[4]["status"] == "normal" and g[5]["status"] == "normal"
          and g[6]["status"] == "swap"
          and g[6]["display_weekday"] == 3
          and len(g[6]["cells"]) == 1
          and g[6]["cells"][3]["course_name"] == "大学英语")
    results.append(("用例3 放假+调休：第5周周六10/10 改上星期三『大学英语』",
                    ok, "10/5-10/7 holiday；10/8-10/9 normal；10/10 swap→display_weekday=3，英语课落在3-4节"))

    # 用例4：关闭规则开关 → 全部还原为 normal
    g = build_grid(FIXTURE_COURSES, FIXTURE_RULES, START, week_no=5, apply_rules=False)
    ok = all(g[d]["status"] == "normal" for d in DAYS) and len(g[6]["cells"]) == 1
    results.append(("用例4 规则开关：关闭后第5周全部还原为正常课表", ok, "状态全为 normal"))

    # 用例5：周日调休（第2周 9/20 周日，调休上星期二的课）
    g = build_grid(FIXTURE_COURSES, FIXTURE_RULES, START, week_no=2)
    ok = (g[7]["status"] == "swap" and g[7]["display_weekday"] == 2
          and len(g[7]["cells"]) == 0)  # 星期二无课程（fixture里周二没课）
    results.append(("用例5 周日调休：第2周9/20(日) 按星期二课表上课",
                    ok, "周日 swap→display_weekday=2；星期二无 fixture 课程故 cells 为空"))

    return results


# ----------------------------------------------------------------------
# 6. 页面渲染
# ----------------------------------------------------------------------
def render_table(grid, courses):
    """把 build_grid 的结果画成 HTML 周课表（节次×星期）。"""
    names = sorted({c["course_name"] for c in courses})
    color_of = {n: COLOR_POOL[i % len(COLOR_POOL)] for i, n in enumerate(names)}

    head = "".join(
        f"<th style='min-width:130px'>{WEEKDAY_CN[wd]}<br>"
        f"<small>{grid[wd]['date'].strftime('%m-%d')}</small></th>"
        for wd in DAYS)
    rows = []
    for sec in range(1, MAX_SECTION + 1):
        tds = [f"<td style='text-align:center;color:#999;width:50px'>{sec}</td>"]
        for wd in DAYS:
            info = grid[wd]
            c = info["cells"].get(sec)
            if info["status"] == "holiday":
                tds.append("<td style='background:#f3f4f6;color:#9ca3af;text-align:center'>放假</td>")
            elif c:
                span = int(c["end_section"]) - int(c["start_section"]) + 1
                swap_tag = "<br><i>🔁 调休</i>" if info["status"] == "swap" else ""
                tds.append(
                    f"<td rowspan='{span}' style='background:{color_of[c['course_name']]};"
                    f"border-radius:6px;padding:4px;font-size:12px'>"
                    f"<b>{c['course_name']}</b><br>{c.get('classroom','')}"
                    f"<br>{c['start_section']}-{c['end_section']}节{swap_tag}</td>")
            else:
                # 被 rowspan 占据的节次不输出空 td
                prev = any(int(x["end_section"]) >= sec > int(x["start_section"])
                           for x in info["cells"].values())
                if not prev:
                    tds.append("<td></td>")
        rows.append("<tr>" + "".join(tds) + "</tr>")

    st.markdown(f"""
    <style>table td, table th {{border:1px solid #e5e7eb;padding:3px;vertical-align:top}}</style>
    <table style='border-collapse:collapse;width:100%;table-layout:fixed'>
    <tr><th>节次</th>{head}</tr>{''.join(rows)}
    </table>""", unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="动态课表原型", layout="wide")
    st.title("📅 动态课表渲染 & 放假/调休规则应用（Web 原型）")

    # ---------- 侧边栏：数据导入与参数 ----------
    with st.sidebar:
        st.header("1️⃣ 课表数据导入")
        up_c = st.file_uploader("上传 courses.json（成员1输出）", type=["json"])
        st.caption("不上传则自动读取脚本同目录下的 courses.json")

        st.header("2️⃣ 调休放假规则")
        up_r = st.file_uploader("上传 adjust_rule.json（成员3输出）", type=["json"])
        st.caption("或在下方『通知截图』处上传截图自动解析")

        st.header("3️⃣ 学期参数")
        start = st.date_input("学期开学日期（周一）", value=dt.date(2026, 9, 7))
        total_weeks = st.number_input("总周数", 1, 30, 18)

    # ---------- 课程数据 ----------
    if up_c:
        data = json.load(up_c)
        if isinstance(data, dict):           # 兼容 {"courses": [...]} 包裹格式
            data = data.get("courses", [])
        courses = data
    else:
        # 课表可能在 data/ 下（新版网站），也可能在脚本同目录（旧版），都找一遍
        p = next((q for q in (Path("data/courses.json"), Path("courses.json")) if q.exists()),
                 Path("data/courses.json"))
        courses = load_courses(p) if p.exists() else []

    # ---------- 规则数据（session_state 可被 OCR/JSON 上传覆盖） ----------
    if "rules" not in st.session_state or up_r:
        if up_r:
            st.session_state["rules"] = json.load(up_r)
        else:
            p = Path("adjust_rule.json")
            st.session_state["rules"] = load_rules(p) if p.exists() \
                else {"holiday_dates": [], "workday_adjust": {}, "course_adjust": []}
    rules = st.session_state["rules"]

    # ---------- 通知截图上传 & OCR 解析 ----------
    with st.expander("📷 上传调休通知截图（自动 OCR 解析规则）", expanded=False):
        shot = st.file_uploader("上传通知截图（png/jpg）", type=["png", "jpg", "jpeg"],
                                key="shot_uploader")
        ocr_text = ""
        if shot is not None:
            img_bytes = shot.getvalue()
            st.image(img_bytes, caption="上传的通知截图", use_column_width=True)
            with st.spinner("正在 OCR 识别..."):
                ocr_text = ocr_image(img_bytes)
            if ocr_text.strip():
                st.success("OCR 识别完成，文本已填入下方")
            else:
                st.info("当前环境未安装 PaddleOCR，OCR 跳过。请将识别文本粘贴到下方。")

        txt = st.text_area("OCR 文本（可手动编辑/粘贴）", value=ocr_text, height=120,
                           placeholder="例如：2026年9月25日至10月7日放假；9月20日（星期日）按10月6日（星期二）课表上课")
        if st.button("🔍 从文本解析规则", disabled=not txt.strip()):
            parsed = parse_rules_from_text(txt)
            st.session_state["rules"] = parsed
            rules = parsed
            st.success(f"解析完成：放假 {len(parsed['holiday_dates'])} 天，"
                       f"调休 {len(parsed['workday_adjust'])} 天")

    # ---------- 规则总览 ----------
    with st.expander("📜 当前放假/调休规则", expanded=True):
        c1, c2 = st.columns(2)
        c1.write(f"**放假 {len(rules['holiday_dates'])} 天**："
                 f"{', '.join(rules['holiday_dates']) or '无'}")
        c2.write("**调休**：" + (", ".join(f"{d}→按{v}上课"
                                          for d, v in rules["workday_adjust"].items()) or "无"))

    # ---------- 周次切换 & 规则开关 ----------
    col1, col2, col3 = st.columns([2, 1, 3])
    week_no = col1.slider("切换周次", 1, int(total_weeks), 1)
    apply_rules = col2.toggle("✅ 应用放假/调休规则", value=True)
    grid = build_grid(courses, rules, start, week_no, apply_rules)
    status_txt = " / ".join(
        f"{WEEKDAY_CN[d]}{'🎉放假' if grid[d]['status']=='holiday' else ('🔁调休' if grid[d]['status']=='swap' else '')}"
        for d in DAYS)
    col3.caption(f"第 {week_no} 周（{grid[1]['date'].strftime('%m-%d')} ~ "
                 f"{grid[7]['date'].strftime('%m-%d')}）\n\n{status_txt}")

    # ---------- 渲染课表 ----------
    if not courses:
        st.warning("未加载到课程数据，请上传 courses.json")
    else:
        render_table(grid, courses)

    # ---------- 测试 & 说明 ----------
    tab1, tab2, tab3 = st.tabs(["🧪 端到端测试", "📖 模块操作说明", "⚠️ 原型局限"])
    with tab1:
        all_pass = True
        for name, ok, detail in run_tests():
            all_pass = all_pass and ok
            (st.success if ok else st.error)(
                f"{'✅ PASS' if ok else '❌ FAIL'}　{name} —— {detail}")
        if all_pass:
            st.balloons()
    with tab2:
        st.markdown("""
**模块功能**：加载课程与调休放假规则，把规则作用到每一天，渲染调整后的动态课表。

**环境配置**：`pip install streamlit pandas python-dateutil pillow`
（如需截图自动 OCR，另装 `paddleocr paddlepaddle`）

**启动**：在脚本所在目录执行 `streamlit run timetable_view.py`

**输入文件**：
- `courses.json`（成员1）：课程列表，`week_range` 形如 `"2-9"`；
- `adjust_rule.json`（成员3）：含 `holiday_dates`、`workday_adjust`。

**操作步骤**：
1. 侧边栏上传 courses.json（或放同目录自动读取）；
2. 侧边栏上传 adjust_rule.json，**或**展开「上传调休通知截图」上传截图自动 OCR 解析规则；
3. 设置开学日期，拖动滑块切换周次；
4. 用「应用放假/调休规则」开关对比规则前后的课表。

**核心规则**：
- 某天属于 `holiday_dates` → 当天所有课程隐藏，显示『放假』；
- 某天属于 `workday_adjust` → 取出对应星期的全部课程放到当天展示（标🔁调休）。
        """)
    with tab3:
        st.markdown("""
1. 截图 OCR 依赖 PaddleOCR；未安装时需手动粘贴 OCR 文本（成员2模块产出）；
2. 文本解析为启发式规则，措辞复杂的正式通知（如连休拼接、跨年月）可能漏识别；
3. 只支持「整日照星期X上课」型调休，不支持半天调休/单门课微调（`course_adjust` 暂未实现）；
4. 节次上限固定 11 节，超出的课程不渲染；
5. 颜色按课程名分配，同名单课程跨教室无法区分。
        """)


if __name__ == "__main__":
    main()
