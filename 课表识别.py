# -*- coding: utf-8 -*-
"""
Created on Wed Oct 21 16:01:06 2026

@author: ASUS
"""

import csv
import json
import re
from typing import List, Dict

# ===================== 1. 全局字段规范（统一JSON格式，全员共用）=====================
COURSE_FIELDS = [
    "course_name",      # 课程名（字符串，不能为空）
    "weekday",          # 星期：1~7（周一到周日，int）
    "start_section",    # 开始节次：1~12（int）
    "end_section",      # 结束节次：>=start_section，1~12（int）
    "week_range",       # 上课周次：字符串，支持格式"1-8,10,12-16"
    "classroom"         # 教室（字符串，允许为空）
]

# 输出JSON固定格式：courses.json，顶层为课程对象数组
OUTPUT_JSON_PATH = "courses.json"
# 输入CSV文件名
INPUT_CSV_PATH = "选课导出.csv"

# 正则校验：周次格式（数字、区间、逗号分隔）
WEEK_REGEX = re.compile(r"^(\d+(-\d+)?)(,\d+(-\d+)?)*$")


# ===================== 2. 数据校验函数 =====================
def validate_course_item(item: Dict) -> (bool, str):
    """单条课程合法性校验，返回(是否合法, 错误信息)"""
    # 1. 字段完整性校验
    for field in COURSE_FIELDS:
        if field not in item:
            return False, f"缺失字段：{field}"

    # 2. 课程名非空
    if not item["course_name"].strip():
        return False, "课程名称不能为空"

    # 3. 星期校验：1~7整数
    try:
        weekday = int(item["weekday"])
        if not 1 <= weekday <= 7:
            return False, f"星期必须为1~7，当前值：{weekday}"
    except ValueError:
        return False, f"星期必须是数字，当前值：{item['weekday']}"

    # 4. 节次校验：1~12，结束>=开始
    try:
        start_sec = int(item["start_section"])
        end_sec = int(item["end_section"])
        if not (1 <= start_sec <= 12 and 1 <= end_sec <= 12):
            return False, "节次必须在1~12之间"
        if end_sec < start_sec:
            return False, f"结束节次{end_sec}小于开始节次{start_sec}"
    except ValueError:
        return False, "开始/结束节次必须为数字"

    # 5. 周次格式校验
    week_str = item["week_range"].strip()
    if not WEEK_REGEX.match(week_str):
        return False, f"周次格式错误，仅支持形如1-8,10,12-16，输入：{week_str}"

    return True, ""


# ===================== 3. CSV读取+清洗+转换 =====================
def read_and_clean_csv(csv_path: str) -> List[Dict]:
    """读取教务CSV，清洗空行、空格，校验数据，返回合法课程列表"""
    valid_courses = []
    error_rows = []

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            row_idx = 1  # 行号从表头下第一行开始

            for row in reader:
                row_idx += 1
                # 去除所有字段首尾空格
                clean_row = {k: v.strip() if isinstance(v, str) else v for k, v in row.items()}

                # 跳过全空行
                if all(v == "" for v in clean_row.values()):
                    print(f"跳过第{row_idx}行空行")
                    continue

                # 字段合法性校验
                ok, msg = validate_course_item(clean_row)
                if ok:
                    # 数值字段转为int
                    clean_row["weekday"] = int(clean_row["weekday"])
                    clean_row["start_section"] = int(clean_row["start_section"])
                    clean_row["end_section"] = int(clean_row["end_section"])
                    valid_courses.append(clean_row)
                else:
                    error_rows.append((row_idx, msg, clean_row))

        # 打印校验结果
        print(f"\n===== 数据校验结果 =====")
        print(f"有效课程条数：{len(valid_courses)}")
        print(f"错误数据条数：{len(error_rows)}")
        for idx, err_msg, data in error_rows:
            print(f"第{idx}行错误：{err_msg}，数据：{data}")

        return valid_courses

    except FileNotFoundError:
        print(f"错误：找不到文件 {csv_path}，请把CSV放在同目录下！")
        return []
    except Exception as e:
        print(f"读取CSV发生未知异常：{str(e)}")
        return []


# ===================== 4. 手动录入备用功能 =====================
def manual_add_course() -> Dict:
    """交互式手动录入单条课程，返回结构化字典"""
    print("\n===== 手动录入课程（备用）=====")
    course = {}
    course["course_name"] = input("课程名称：").strip()
    course["weekday"] = input("星期(1-7)：").strip()
    course["start_section"] = input("开始节次(1-12)：").strip()
    course["end_section"] = input("结束节次(1-12)：").strip()
    course["week_range"] = input("上课周次(例：1-8,10)：").strip()
    course["classroom"] = input("教室：").strip()

    # 自动校验录入内容
    ok, msg = validate_course_item(course)
    if not ok:
        print(f"录入失败：{msg}")
        return None
    # 类型转换
    course["weekday"] = int(course["weekday"])
    course["start_section"] = int(course["start_section"])
    course["end_section"] = int(course["end_section"])
    print("课程录入成功！")
    return course


# ===================== 5. 导出JSON文件 =====================
def export_to_json(course_list: List[Dict], json_path: str):
    """把课程列表导出为标准courses.json"""
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(course_list, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 成功导出标准课表文件：{json_path}")
        return True
    except Exception as e:
        print(f"❌ JSON导出失败：{str(e)}")
        return False


# ===================== 主程序入口 =====================
if __name__ == "__main__":
    all_courses = []

    # 第一步：读取CSV导入数据
    print("正在读取选课CSV文件...")
    csv_courses = read_and_clean_csv(INPUT_CSV_PATH)
    all_courses.extend(csv_courses)

    # 第二步：可选手动追加课程
    while True:
        choice = input("\n是否需要手动添加课程？(y/n)：").strip().lower()
        if choice == "y":
            new_course = manual_add_course()
            if new_course:
                all_courses.append(new_course)
        else:
            break

    # 第三步：输出JSON
    if all_courses:
        export_to_json(all_courses, OUTPUT_JSON_PATH)
        print(f"最终共计 {len(all_courses)} 条课程数据")
    else:
        print("无有效课程数据，不生成JSON文件")
