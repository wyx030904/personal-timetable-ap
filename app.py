# -*- coding: utf-8 -*-
"""
智能课表助手 · Flask 后端
────────────────────────────────────────────────
• 图片识别：上传放假/调课通知截图 → OCR → 自动抽取调休规则（PaddleOCR）
• 文字调课：直接输入一句话 → 解析成调整操作 → 预览 → 一键应用
• 课表计算：基础课表 + 调整规则 → 任意周/任意日期的实际课表
启动： python app.py     然后打开 http://localhost:8080
"""
import os
import io
import csv
import json
import copy
import threading
import webbrowser
from datetime import date, timedelta

from flask import Flask, request, jsonify, send_from_directory

import engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Vercel Serverless 函数的文件系统只读（仅 /tmp 可写），通过环境变量切换数据目录
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
STATIC_DIR = os.path.join(BASE_DIR, "static")


def _ensure_writable_data_dir():
    """若 DATA_DIR 不可写（如 Vercel 只读文件系统），自动回退到 /tmp/data"""
    global DATA_DIR, COURSES_FILE, ADJUST_FILE, SETTINGS_FILE
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        # 尝试写一个临时文件验证可写
        _probe = os.path.join(DATA_DIR, ".write_test")
        with open(_probe, "w") as f:
            f.write("ok")
        os.remove(_probe)
    except Exception:
        DATA_DIR = "/tmp/data"
        os.makedirs(DATA_DIR, exist_ok=True)
    finally:
        COURSES_FILE = os.path.join(DATA_DIR, "courses.json")
        ADJUST_FILE = os.path.join(DATA_DIR, "adjustments.json")
        SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")


_ensure_writable_data_dir()

# 若 DATA_DIR 指向可写目录（如 /tmp），把打包时的初始数据复制过去，保证首次启动有课表数据
_SEED_DIR = os.path.join(BASE_DIR, "data")
if os.path.isdir(_SEED_DIR) and os.path.abspath(_SEED_DIR) != os.path.abspath(DATA_DIR):
    import shutil
    for name in ("courses.json", "adjustments.json", "settings.json"):
        src = os.path.join(_SEED_DIR, name)
        dst = os.path.join(DATA_DIR, name)
        if os.path.isfile(src) and not os.path.isfile(dst):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

# 默认节次时间表（12节，若导入文件未含时间则用此兜底）
DEFAULT_SECTION_TIMES = [
    {"start": "08:00", "end": "08:50"}, {"start": "08:55", "end": "09:45"},
    {"start": "10:00", "end": "10:50"}, {"start": "10:55", "end": "11:45"},
    {"start": "14:00", "end": "14:50"}, {"start": "14:55", "end": "15:45"},
    {"start": "16:00", "end": "16:50"}, {"start": "16:55", "end": "17:45"},
    {"start": "19:00", "end": "19:50"}, {"start": "19:55", "end": "20:45"},
    {"start": "20:50", "end": "21:40"}, {"start": "21:45", "end": "22:35"},
]
DEFAULT_SETTINGS = {
    "term_start": "2026-09-07", "year": 2026, "total_weeks": 20,
    "section_times": DEFAULT_SECTION_TIMES,
}

# ────────────────────────── 数据读写 ──────────────────────────


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # Vercel /tmp 偶尔不可写时静默失败，不影响接口返回
        pass


def get_courses():
    return load_json(COURSES_FILE, [])


def get_adjustments():
    return load_json(ADJUST_FILE, [])


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(load_json(SETTINGS_FILE, {}))
    return s


def _term_start():
    return engine.to_date(get_settings()["term_start"])


# ────────────────────────── 变更预览（diff） ──────────────────────────


def diff_ops(ops, courses=None, adjustments=None, settings=None):
    """把 ops 试算一遍，返回受影响日期的前后对比"""
    courses = get_courses() if courses is None else courses
    adjustments = get_adjustments() if adjustments is None else adjustments
    settings = get_settings() if settings is None else settings
    term_start = engine.to_date(settings["term_start"])
    total = int(settings.get("total_weeks", 20))

    trial = copy.deepcopy(adjustments)
    for op in ops:
        trial.append({
            "id": engine.new_id(),
            "kind": op["kind"],
            "label": op.get("label", ""),
            "payload": op.get("payload", {}),
            "source": op.get("source", "manual"),
        })

    changes = []
    for w in range(1, total + 1):
        monday = engine.week_monday(term_start, w)
        for i in range(7):
            d = monday + timedelta(days=i)
            before = engine.render_date(d, courses, adjustments, term_start)
            after = engine.render_date(d, courses, trial, term_start)
            b = [f"{x['course_name']}({x['start']}-{x['end']}节)" for x in before["items"]
                 if x["status"] != "moved_out"]
            a = [f"{x['course_name']}({x['start']}-{x['end']}节)" for x in after["items"]
                 if x["status"] != "moved_out"]
            if b != a:
                changes.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "week": w,
                    "weekday": d.isoweekday(),
                    "flags": after["flags"],
                    "removed": [x for x in b if x not in a],
                    "added": [x for x in a if x not in b],
                })
    return changes


# ────────────────────────── 规则格式转换 ──────────────────────────
_WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _ops_to_rules(ops):
    """把 engine 的 ops（holiday/makeup）转成前端需要的 {holiday_dates, workday_adjust}"""
    holiday_dates = []
    workday_adjust = {}
    for op in ops:
        kind = op.get("kind")
        payload = op.get("payload", {})
        if kind == "holiday":
            holiday_dates.extend(payload.get("dates", []))
        elif kind == "makeup":
            d = payload.get("date")
            wd = payload.get("follow_weekday")
            if d and wd:
                workday_adjust[d] = _WEEKDAY_NAMES[wd - 1]
    return {"holiday_dates": holiday_dates, "workday_adjust": workday_adjust, "course_adjust": []}


# ────────────────────────── Flask ──────────────────────────
app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    # index.html 放在项目根目录（不在 static/ 下），直接从 BASE_DIR 服务
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/courses.json")
def serve_courses_json():
    return send_from_directory(BASE_DIR, "courses.json")


@app.route("/adjust_rule.json")
def serve_adjust_rule_json():
    return send_from_directory(BASE_DIR, "adjust_rule.json")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


@app.route("/api/state")
def api_state():
    return jsonify({
        "courses": get_courses(),
        "adjustments": get_adjustments(),
        "settings": get_settings(),
        "today": date.today().strftime("%Y-%m-%d"),
    })


@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json() or {}
    s = get_settings()
    for k in ("term_start", "year", "total_weeks"):
        if k in data and data[k] not in (None, ""):
            s[k] = data[k]
    # 节次时间表：允许整体替换（前端自定义）
    if "section_times" in data and isinstance(data["section_times"], list):
        s["section_times"] = data["section_times"]
    save_json(SETTINGS_FILE, s)
    return jsonify({"settings": s})


@app.route("/api/courses", methods=["POST"])
def api_save_courses():
    data = request.get_json() or {}
    courses = data.get("courses", [])
    for c in courses:
        c["weekday"] = int(c["weekday"])
        c["start_section"] = int(c["start_section"])
        c["end_section"] = int(c["end_section"])
    save_json(COURSES_FILE, courses)
    return jsonify({"courses": courses, "count": len(courses)})


@app.route("/api/timetable")
def api_timetable():
    week = request.args.get("week")
    day = request.args.get("date")
    courses, adjustments, term_start = get_courses(), get_adjustments(), _term_start()
    if day:
        return jsonify(engine.render_date(engine.to_date(day), courses, adjustments, term_start))
    week = int(week or 1)
    return jsonify(engine.render_week(week, courses, adjustments, term_start))


@app.route("/api/parse", methods=["POST"])
def api_parse():
    """文字 → 调整操作（不落盘，只做识别）"""
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "请输入内容"}), 400
    mode = data.get("mode", "auto")
    year = int(get_settings().get("year", 2026))
    if mode == "notice":
        res = engine.parse_notice(text, year)
    elif mode == "command":
        res = engine.parse_command(text, get_courses(), year=year)
    else:
        res = engine.parse_text_auto(text, get_courses(), year)
    res["text"] = text
    return jsonify(res)


@app.route("/api/parse_rules", methods=["POST"])
def api_parse_rules():
    """文字 → 前端规则格式 {holiday_dates, workday_adjust}（前端「粘贴通知文本」按钮调用）"""
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "请输入内容"}), 400
    year = int(get_settings().get("year", 2026))
    res = engine.parse_notice(text, year)
    return jsonify(_ops_to_rules(res.get("ops", [])))


@app.route("/api/preview", methods=["POST"])
def api_preview():
    """试算变更，返回前后对比"""
    data = request.get_json() or {}
    ops = data.get("ops", [])
    if not ops:
        return jsonify({"changes": []})
    return jsonify({"changes": diff_ops(ops), "op_count": len(ops)})


@app.route("/api/apply", methods=["POST"])
def api_apply():
    """确认并写入调整规则"""
    data = request.get_json() or {}
    ops = data.get("ops", [])
    source = data.get("source", "manual")
    adjustments = get_adjustments()
    added = []
    for op in ops:
        rec = {
            "id": engine.new_id(),
            "kind": op["kind"],
            "label": op.get("label", ""),
            "payload": op.get("payload", {}),
            "source": source,
            "created_at": date.today().strftime("%Y-%m-%d %H:%M"),
        }
        adjustments.append(rec)
        added.append(rec)
    save_json(ADJUST_FILE, adjustments)
    return jsonify({"added": added, "adjustments": adjustments})


@app.route("/api/adjustments/<adj_id>", methods=["DELETE"])
def api_del_adjustment(adj_id):
    adjustments = [a for a in get_adjustments() if a.get("id") != adj_id]
    save_json(ADJUST_FILE, adjustments)
    return jsonify({"adjustments": adjustments})


@app.route("/api/adjustments/clear", methods=["POST"])
def api_clear_adjustments():
    save_json(ADJUST_FILE, [])
    return jsonify({"adjustments": []})


@app.route("/api/import_courses", methods=["POST"])
@app.route("/api/import_csv", methods=["POST"])
def api_import_courses():
    """导入课表：支持 .docx / .xlsx / .xls / .ics / .txt / .csv"""
    if "file" not in request.files:
        return jsonify({"error": "未收到文件"}), 400
    f = request.files["file"]
    raw = f.read()
    if not raw:
        return jsonify({"error": "文件为空"}), 400
    name = (f.filename or "").lower()

    # ── CSV 分支（字段格式固定，单独处理） ──
    if name.endswith(".csv"):
        try:
            text_stream = io.StringIO(raw.decode("utf-8-sig", errors="replace"))
            reader = csv.DictReader(text_stream)
            valid, errors = [], []
            for i, row in enumerate(reader, start=2):
                row = {k: (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k}
                if not any(row.values()):
                    continue
                need = ["course_name", "weekday", "start_section", "end_section", "week_range"]
                if any(f not in row for f in need):
                    errors.append({"row": i, "error": "缺字段", "data": row})
                    continue
                try:
                    item = {
                        "course_name": row["course_name"],
                        "weekday": int(row["weekday"]),
                        "start_section": int(row["start_section"]),
                        "end_section": int(row["end_section"]),
                        "week_range": row["week_range"],
                        "classroom": row.get("classroom", ""),
                    }
                except ValueError:
                    errors.append({"row": i, "error": "数字字段非法", "data": row})
                    continue
                valid.append(item)
        except Exception as e:
            return jsonify({"error": f"CSV 解析失败：{e}"}), 500

        replaced = 0
        if valid:
            prev = get_courses()
            replaced = len(prev)
            if prev:
                save_json(os.path.join(DATA_DIR, "courses_prev.json"), prev)
            save_json(COURSES_FILE, valid)
        return jsonify({"courses": valid, "valid_count": len(valid), "error_count": len(errors),
                        "errors": errors, "source": "csv", "replaced": replaced,
                        "section_times": None})

    if name.endswith(".doc"):
        return jsonify({"error": "不支持旧版 .doc，请在 Word/WPS 中另存为 .docx 后再导入"}), 400

    # ── 统一解析：docx / xlsx / ics / txt ──
    try:
        import timetable_to_csv
        valid, section_times = timetable_to_csv.parse_file_bytes(raw, name)
    except ValueError as e:
        msg = str(e)
        if "zip" in msg.lower() or "PackageNotFound" in msg:
            return jsonify({"error": "文件不是有效的 .docx（可能是旧版 .doc 或仅改了后缀），请在 Word/WPS 中另存为 .docx"}), 400
        return jsonify({"error": f"解析失败：{msg}"}), 400
    except Exception as e:
        return jsonify({"error": f"解析失败：{type(e).__name__}: {e}"}), 500

    if not valid:
        return jsonify({"error": "没有解析到课程，请确认上传的是课表文件（含“星期一…星期日”表头）"}), 400

    prev = get_courses()
    if prev:
        save_json(os.path.join(DATA_DIR, "courses_prev.json"), prev)
    save_json(COURSES_FILE, valid)

    # 若文件中解析到节次时间，同步写入设置
    src_format = "docx" if name.endswith(".docx") else (
        "xlsx" if name.endswith((".xlsx", ".xls")) else
        "ics" if name.endswith(".ics") else "txt")
    if section_times:
        s = get_settings()
        s["section_times"] = section_times
        save_json(SETTINGS_FILE, s)

    return jsonify({
        "courses": valid, "valid_count": len(valid), "error_count": 0,
        "errors": [], "source": src_format,
        "replaced": len(prev),
        "section_times": section_times,
    })


# ────────────────────────── OCR ──────────────────────────
_ocr_engine = None
_ocr_kind = None
_ocr_error = None
_ocr_lock = threading.Lock()


def _to_array(image_bytes):
    """把上传的图片字节解码成 ndarray（PaddleOCR 不接受裸 bytes）"""
    try:
        import numpy as np
    except Exception:
        return None
    try:
        import cv2
        arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None:
            return arr
    except Exception:
        pass
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return np.array(img)[:, :, ::-1]  # RGB -> BGR
    except Exception:
        return None


def get_ocr():
    """加载 OCR 引擎，优先 PaddleOCR，其次 RapidOCR，都失败才报错"""
    global _ocr_engine, _ocr_kind, _ocr_error
    if _ocr_engine is not None:
        return _ocr_engine, _ocr_kind
    if _ocr_error:
        raise RuntimeError(_ocr_error)
    with _ocr_lock:
        if _ocr_engine is not None:      # 预热线程可能刚好加载完
            return _ocr_engine, _ocr_kind
        return _build_ocr()


def _build_ocr():
    # 1) PaddleOCR —— 先试 3.x 新接口，再退回 2.x 旧接口
    try:
        # paddle 3.x 在部分 Windows CPU 上 oneDNN 内核会崩（ConvertPirAttribute2RuntimeAttribute），
        # 关掉 MKLDNN 走原生 CPU 推理，速度略慢但稳定
        os.environ.setdefault("FLAGS_use_mkldnn", "0")
        from paddleocr import PaddleOCR
        common = dict(lang="ch", use_doc_orientation_classify=False,
                      use_doc_unwarping=False, use_textline_orientation=False,
                      enable_mkldnn=False)
        try:
            # 轻量 mobile 模型：CPU 上单张截图几秒出结果，精度足够识别通知截图
            _ocr_engine = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det",
                                    text_recognition_model_name="PP-OCRv5_mobile_rec",
                                    **common)
            _ocr_kind = "paddle3"
        except TypeError:
            # paddleocr 2.x 没有这些参数，退回旧接口
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            _ocr_kind = "paddle2"
        except Exception:
            # mobile 模型下载失败等，用默认模型
            _ocr_engine = PaddleOCR(**common)
            _ocr_kind = "paddle3"
        return _ocr_engine, _ocr_kind
    except ImportError as e:
        _paddle_err = f"No module named 'paddleocr'（{e}）"
    except Exception as e:
        _paddle_err = f"{type(e).__name__}: {e}"

    # 2) RapidOCR —— 纯 onnxruntime 实现，无需 paddle
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
        _ocr_kind = "rapid"
        return _ocr_engine, _ocr_kind
    except Exception:
        pass

    _ocr_error = ("未能加载 OCR 引擎：" + _paddle_err +
                  "。请在项目环境执行 pip install paddlepaddle paddleocr"
                  "（或 pip install rapidocr-onnxruntime），"
                  "也可以先用「粘贴通知文字」功能。")
    raise RuntimeError(_ocr_error)


def _lines_from_paddle(res, kind):
    """统一把两种 PaddleOCR 返回值整理成 [(y, text, score), ...]"""
    out = []
    if not res:
        return out
    pages = res if isinstance(res, (list, tuple)) else [res]
    for page in pages:
        # 3.x：OCRResult，可用 ['rec_texts'] / ['rec_scores'] 取值
        try:
            texts = page["rec_texts"]
            scores = page["rec_scores"]
            boxes = page.get("rec_boxes") if hasattr(page, "get") else None
            if boxes is None and hasattr(page, "get"):
                boxes = page.get("rec_polys") or page.get("dt_polys")
            for i, t in enumerate(texts):
                try:
                    b = boxes[i]
                    y = float(b[0][1]) if hasattr(b[0], "__len__") else float(b[1])
                except Exception:
                    y = i * 1000.0
                out.append((y, str(t), float(scores[i])))
            continue
        except Exception:
            pass
        # 2.x：[[ [box, (text, score)], ... ]]
        if kind == "paddle2" and isinstance(page, (list, tuple)):
            for line in page:
                try:
                    box, (t, s) = line[0], line[1]
                    y = float(box[0][1])
                except Exception:
                    continue
                out.append((y, str(t), float(s)))
    out.sort(key=lambda x: x[0])
    return out


def _lines_from_rapid(res):
    out = []
    for box, text, score in (res or []):
        try:
            out.append((float(box[0][1]), str(text), float(score)))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def ocr_image(image_bytes):
    """返回 (文本, 平均置信度)"""
    ocr, kind = get_ocr()
    arr = _to_array(image_bytes)
    if kind == "rapid":
        lines = _lines_from_rapid(ocr(arr if arr is not None else image_bytes))
    else:
        # 兼容不同 PaddleOCR 版本：3.x 部分版本用 predict，部分用 ocr；2.x 用 ocr(cls=True)
        img_input = arr if arr is not None else image_bytes
        res = None
        if kind == "paddle3":
            if hasattr(ocr, "predict"):
                res = ocr.predict(img_input)
            elif hasattr(ocr, "ocr"):
                res = ocr.ocr(img_input)
        else:
            res = ocr.ocr(arr, cls=True)
        lines = _lines_from_paddle(res, kind)
    if not lines:
        return "", 0.0
    text = "\n".join(t for _, t, _ in lines)
    avg = sum(s for _, _, s in lines) / len(lines) * 100
    return text, avg


@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    if "image" not in request.files:
        return jsonify({"error": "未收到图片文件"}), 400
    try:
        text, conf = ocr_image(request.files["image"].read())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"text": text, "confidence": round(conf, 2)})


@app.route("/api/ocr_parse", methods=["POST"])
def api_ocr_parse():
    """上传图片一步到位：OCR → 抽取调休规则"""
    if "image" not in request.files:
        return jsonify({"error": "未收到图片文件"}), 400
    try:
        text, conf = ocr_image(request.files["image"].read())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    year = int(get_settings().get("year", 2026))
    res = engine.parse_text_auto(text, get_courses(), year)
    res.update({"text": text, "confidence": round(conf, 2)})
    return jsonify(res)


@app.route("/api/ocr_and_parse", methods=["POST"])
def api_ocr_and_parse():
    """上传图片一步到位：OCR → 规则（前端「上传调休通知截图」按钮调用）。
    Vercel 上未装 paddle，OCR 会失败并返回错误，但不影响其他功能。"""
    if "image" not in request.files:
        return jsonify({"error": "未收到图片文件"}), 400
    try:
        text, conf = ocr_image(request.files["image"].read())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    year = int(get_settings().get("year", 2026))
    res = engine.parse_notice(text, year)
    rules = _ops_to_rules(res.get("ops", []))
    return jsonify({"text": text, "confidence": round(conf, 2), "rules": rules})


def _warmup_ocr():
    """后台预热 OCR 模型，避免用户第一次上传时卡十几秒"""
    try:
        engine_obj, kind = get_ocr()
        print(f"[OCR] 引擎就绪：{kind}")
    except Exception as e:
        print(f"[OCR] 不可用，图片识别将提示改用粘贴文字：{e}")


def main():
    save_json(SETTINGS_FILE, get_settings())
    port = int(os.environ.get("PORT", 8080))
    url = f"http://localhost:{port}/"
    print(f"智能课表助手已启动：{url}")
    print("按 Ctrl+C 停止服务")
    threading.Thread(target=_warmup_ocr, daemon=True).start()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
