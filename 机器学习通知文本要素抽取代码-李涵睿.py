# -*- coding: utf-8 -*-
"""
模块：校园通知要素抽取 notice_extract.py
支持两种通知类型：
① 节假日放假调休通知（解析放假区间、调休补课）
② 课程调课通知（停课、顺延、补课时间、周次+节次）
对接成员2 OCR输出：读取 ocr_result.txt
输出 adjust_rule.json
"""
import re
import json
import jieba
from dateutil import parser
from datetime import datetime, timedelta

# ====================== 配置区 ======================
INPUT_TXT = "ocr_result.txt"
OUTPUT_JSON = "adjust_rule.json"
NOTICE_YEAR = 2026
WEEK_LIST = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
             "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# ====================================================

def clean_text(raw: str) -> str:
    """文本清洗：去除多余换行、空格、表情、特殊符号"""
    text = raw.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[※★■●◆🌹！?？]", "", text)
    return text.strip()


def parse_one_date(date_str: str) -> datetime | None:
    """解析单个日期，补全年份"""
    try:
        dt = parser.parse(date_str, fuzzy=True, default=datetime(NOTICE_YEAR, 1, 1))
        return dt
    except Exception:
        return None


def expand_date_range(text: str) -> list[str]:
    """解析日期区间「A至B」，展开全部放假日期"""
    holiday_days = []
    pat_range = re.compile(r"(.+?)[至‑—](.+?)[，。；\s]")
    matches = pat_range.findall(text)
    for start_str, end_str in matches:
        dt_start = parse_one_date(start_str)
        dt_end = parse_one_date(end_str)
        if dt_start is None or dt_end is None:
            continue
        cur = dt_start
        while cur <= dt_end:
            holiday_days.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
    return holiday_days


def extract_holiday_rule(cleaned_text: str, raw_original_text: str) -> dict:
    range_holiday = expand_date_range(cleaned_text)
    single_dates = []
    pat_date = re.compile(r"(\d{1,2}月\d{1,2}日)")
    for ds in pat_date.findall(cleaned_text):
        dt = parse_one_date(ds)
        if dt:
            single_dates.append(dt.strftime("%Y-%m-%d"))
    holiday_total = sorted(list(set(range_holiday + single_dates)))

    adjust_map = {}
    # 按换行分割原始文本，遍历每一行
    lines = raw_original_text.splitlines()
    for line in lines:
        line_strip = line.strip()
        if "课表上课" not in line_strip:
            continue
        # 提取调休上班日期
        date_match = re.search(r"(\d{1,2}月\d{1,2}日)", line_strip)
        if not date_match:
            continue
        work_date_str = date_match.group(1)
        dt_work = parse_one_date(work_date_str)
        if not dt_work:
            continue
        std_work = dt_work.strftime("%Y-%m-%d")
        # 在这一行找目标星期（按xxx课表上课后面的星期）
        target_week = None
        idx = line_strip.find("按")
        if idx != -1:
            sub = line_strip[idx:]
            for w in WEEK_LIST:
                if w in sub:
                    target_week = w
                    break
        if target_week is not None:
            adjust_map[std_work] = target_week

    # 移除调休上班日
    work_day_set = set(adjust_map.keys())
    holiday_total = [d for d in holiday_total if d not in work_day_set]

    return {
        "type": "holiday_notice",
        "holiday_dates": holiday_total,
        "workday_adjust": adjust_map,
        "course_adjust": []
    }


def extract_course_rule(text: str) -> dict:
    """解析课程调课通知（停课、顺延、周次、时段）"""
    course_adjust_list = []
    pat_week_section = re.compile(r"(\d+)周(周[一二三四五六日]).*?(\d+)[-～](\d+)")
    matches = pat_week_section.findall(text)
    for week_num, week_name, start_sec, end_sec in matches:
        course_adjust_list.append({
            "week": int(week_num),
            "weekday": week_name,
            "section_start": int(start_sec),
            "section_end": int(end_sec),
            "action": "reschedule"
        })
    is_stop = "停课" in text or "停一次" in text
    return {
        "type": "course_notice",
        "holiday_dates": [],
        "workday_adjust": {},
        "course_adjust": course_adjust_list,
        "is_stop_course": is_stop
    }


def judge_notice_type(text: str):
    if "放假" in text:
        return "holiday"
    elif ("停课" in text or "顺延" in text or "周" in text and ("晚上" in text or "节" in text)):
        return "course"
    else:
        return "unknown"


def run_from_text(raw_text: str):
    clean = clean_text(raw_text)
    list(jieba.cut(clean))
    n_type = judge_notice_type(clean)
    if n_type == "holiday":
        res = extract_holiday_rule(clean, raw_text)
    elif n_type == "course":
        res = extract_course_rule(clean)
    else:
        res = {
            "type": "unknown",
            "holiday_dates": [],
            "workday_adjust": {},
            "course_adjust": []
        }
    return res


def main():
    print(f"【notice_extract】读取输入文件 {INPUT_TXT}")
    try:
        with open(INPUT_TXT, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except FileNotFoundError:
        print(f"错误：未找到 {INPUT_TXT}")
        print("请将成员2 OCR识别结果保存为 ocr_result.txt 放在同级目录！")
        return
    # --------调试打印，测试完可以删掉--------
    print("====原始文本内容DEBUG====")
    print(raw_text)
    print("========================")
    result_data = run_from_text(raw_text)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as fw:
        json.dump(result_data, fw, ensure_ascii=False, indent=2)
    print("====抽取结果====")
    print(json.dumps(result_data, ensure_ascii=False, indent=2))
    print(f"\n✅ 结果已写入 {OUTPUT_JSON}，交付成员4")


if __name__ == "__main__":
    main()