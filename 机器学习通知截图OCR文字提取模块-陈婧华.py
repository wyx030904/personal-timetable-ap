# -*- coding: utf-8 -*-
"""
机器学习通知截图 OCR 文字提取模块（陈婧华）
────────────────────────────────────────
输入放假/调课通知截图，输出按版面从上到下排列的纯文本，并报告平均置信度。
兼容 paddleocr 2.x 与 3.x 两套接口（3.x 的初始化参数、调用方法、返回结构都变了）。
"""
import os
import sys

os.environ.setdefault("FLAGS_use_mkldnn", "0")  # paddle 3.x 在部分 Windows CPU 上 oneDNN 内核会崩

from paddleocr import PaddleOCR  # noqa: E402  (需先设置上面的环境变量)


class UniversalOCR:
    def __init__(self):
        print("[INFO] 正在初始化通用 PaddleOCR 引擎...")
        try:
            # paddleocr 3.x：轻量 mobile 模型，CPU 上单张截图几秒出结果
            # enable_mkldnn=False：绕过 oneDNN 内核 bug（ConvertPirAttribute2RuntimeAttribute）
            self.ocr = PaddleOCR(
                lang="ch",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="PP-OCRv5_mobile_rec",
            )
            self.version = 3
        except TypeError:
            # paddleocr 2.x：没有上面那些参数
            self.ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            self.version = 2
        print(f"[INFO] PaddleOCR {self.version}.x 引擎就绪")

    @staticmethod
    def _read_image(image_path: str):
        """把图片读成 ndarray。cv2.imread 读不了含中文的路径，改用字节解码"""
        with open(image_path, "rb") as f:
            raw = f.read()
        try:
            import numpy as np
            import cv2
            arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if arr is not None:
                return arr
        except ImportError:
            pass
        from PIL import Image
        import numpy as np
        img = Image.open(image_path).convert("RGB")
        return np.array(img)[:, :, ::-1]  # RGB -> BGR

    def recognize_with_confidence(self, image_path: str) -> str:
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"找不到图片文件: {image_path}")

        image = self._read_image(image_path)
        extracted_lines = []   # (y坐标, 文字, 置信度)
        total_conf = 0.0

        if self.version == 3:
            # 3.x：predict() 返回 OCRResult，用 rec_texts / rec_scores / rec_boxes 取值
            for res in self.ocr.predict(image):
                texts = res["rec_texts"]
                scores = res["rec_scores"]
                try:
                    boxes = res["rec_boxes"]  # [N, 4]：(x1, y1, x2, y2)
                except Exception:
                    boxes = None
                for i, t in enumerate(texts):
                    y = float(boxes[i][1]) if boxes is not None else float(i)
                    extracted_lines.append((y, t, float(scores[i])))
        else:
            # 2.x：[[ [box, (text, score)], ... ]]
            result = self.ocr.ocr(image, cls=True)
            if result and result[0]:
                for line in result[0]:
                    box, (text, conf) = line[0], line[1]
                    extracted_lines.append((float(box[0][1]), text, float(conf)))

        # 按纵坐标从上到下排序，还原通知原文的阅读顺序
        extracted_lines.sort(key=lambda x: x[0])

        # 计算整张图的平均置信度
        count = len(extracted_lines)
        total_conf = sum(c for _, _, c in extracted_lines)
        avg_conf = (total_conf / count * 100) if count > 0 else 0

        print("\n================== 【质量评估提示】 ==================")
        print(f"当前图片平均识别置信度: {avg_conf:.2f}%")
        print("======================================================")

        return "\n".join(t for _, t, _ in extracted_lines)


if __name__ == "__main__":
    ocr_engine = UniversalOCR()

    # 你可以在这里输入任意图片的路径
    img_path = input("请输入要识别的图片路径（例如 test_images/chat_test.jpg）：").strip().strip('"')

    try:
        text = ocr_engine.recognize_with_confidence(img_path)

        print("\n================== 【OCR 提取出的文本】 ==================")
        print(text)
        print("==========================================================")

    except Exception as e:
        print(f"\n[ERROR] 识别出错: {e}")
