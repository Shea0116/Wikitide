"""Offline OCR: PDFium rendering, then Tesseract or Apple's local Vision framework."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def available():
    return bool(importlib.util.find_spec('pypdfium2') and (shutil.which('tesseract') or (sys.platform=='darwin' and shutil.which('swift'))))


def recognize_pdf_page(raw,index):
    if not available(): raise ValueError('本地 OCR 尚未配置，请安装 PDFium 与 Tesseract（macOS 也可使用 Vision）')
    import pypdfium2
    with tempfile.TemporaryDirectory(prefix='wikitide-ocr-') as directory:
        image_path=Path(directory)/'page.png'
        document=pypdfium2.PdfDocument(raw)
        try:
            page=document[index]
            width,height=page.get_size()
            if width*height>2000000: raise ValueError('PDF 页面尺寸过大，无法 OCR')
            bitmap=page.render(scale=2)
            try: bitmap.to_pil().save(image_path)
            finally: bitmap.close();page.close()
        finally: document.close()
        tesseract=shutil.which('tesseract')
        if tesseract:
            language=os.getenv('KB_OCR_LANGUAGES','chi_sim+eng')
            if not all(char.isalnum() or char in '_+' for char in language): raise ValueError('OCR 语言配置无效')
            result=subprocess.run([tesseract,str(image_path),'stdout','-l',language],capture_output=True,timeout=25)
            if result.returncode: raise ValueError('OCR 识别失败，请检查语言包配置')
            return result.stdout.decode('utf-8')
        result=subprocess.run(['swift','-module-cache-path',str(Path(directory)/'modules'),str(Path(__file__).with_name('scripts')/'vision_ocr.swift'),str(image_path)],capture_output=True,timeout=45)
        if result.returncode: raise ValueError('macOS 本地 OCR 失败，请检查 Vision 与 Swift 运行环境')
        return json.loads(result.stdout)['text']
