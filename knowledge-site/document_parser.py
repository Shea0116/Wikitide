"""Text extraction in a short-lived resource-limited subprocess; never executes document content."""
import io
import json
import sys
import zipfile
import hashlib
import posixpath
import re
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_TEXT = 100000
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def extract(raw, suffix, *, ocr=False, pdf_note=False):
    warnings = []; assets=[]
    if suffix == '.md':
        try:
            body = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('Markdown 请使用 UTF-8 编码') from exc
        warnings.append('相对图片和附件不会自动导入；请在入库前检查链接。')
    elif suffix == '.zip':
        from import_assets import markdown_package
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            body,assets,warnings=markdown_package(archive)
        warnings.append('附件包仅导入正文实际引用的受支持附件；请核对图片和链接。')
    elif suffix == '.pdf':
        from pypdf import PdfReader
        if not raw.startswith(b'%PDF-'):
            raise ValueError('文件不是有效的 PDF')
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ValueError('暂不支持加密 PDF，请先解密后上传')
        if len(reader.pages) > 200:
            raise ValueError('PDF 最多支持 200 页')
        if not len(reader.pages):
            raise ValueError('PDF 没有可阅读的页面')
        if pdf_note:
            return {'body': '', 'assets': [], 'warnings': ['已保留 PDF 原文与排版；需要可编辑正文时，可另行提取文字或转为 Markdown。']}
        parts, size, has_text = [], 0, False
        ocr_pages=0
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ''
            if ocr and not text.strip():
                ocr_pages+=1
                if ocr_pages>20: raise ValueError('每份资料最多 OCR 20 页，请拆分扫描件')
                from local_ocr import recognize_pdf_page
                text=recognize_pdf_page(raw,index)
            has_text = has_text or bool(text.strip())
            size += len(text)
            if size > MAX_TEXT:
                raise ValueError('提取正文超过 100,000 字符，请拆分文件')
            parts.append(f'## 第 {index+1} 页\n\n{text}')
        if not has_text:
            raise ValueError('未提取到文字；扫描件请先进行 OCR')
        body = '\n\n'.join(parts)
        warnings.append('PDF 仅提取文字，不保留图片和原排版。'+(f' 本地 OCR 识别了 {ocr_pages} 页，请逐页核对文字。' if ocr_pages else '扫描页可选择本地 OCR 后重新上传。'))
    elif suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            from import_assets import archive_members,asset
            archive_members(archive)
            files = archive.infolist()
            if len(files) > 2000 or sum(f.file_size for f in files) > 30 * 1024 * 1024:
                raise ValueError('DOCX 解压体积或文件数超过限制')
            if len([f for f in files if f.filename == 'word/document.xml']) != 1:
                raise ValueError('DOCX 缺少或重复正文文件')
            xml = archive.read('word/document.xml')
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError('不支持带实体声明的文档')
            tree = ET.fromstring(xml)
            relationships={}
            if 'word/_rels/document.xml.rels' in archive.namelist():
                relxml=archive.read('word/_rels/document.xml.rels')
                if b'<!DOCTYPE' in relxml.upper() or b'<!ENTITY' in relxml.upper(): raise ValueError('不支持实体声明')
                for rel in ET.fromstring(relxml):
                    if rel.get('TargetMode')=='External': continue
                    target=posixpath.normpath(posixpath.join('word',rel.get('Target','')))
                    if target.startswith('word/media/') and target in archive.namelist(): relationships[rel.get('Id')]=target
            image_refs={}
            for node in tree.iter():
                if node.tag.endswith('}blip'):
                    rid=node.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                    target=relationships.get(rid)
                    if target and target not in image_refs:
                        suffix_image=Path(target).suffix.lower()
                        if suffix_image not in {'.png','.jpg','.jpeg','.gif','.webp'}:
                            warnings.append('未导入不支持的图片：'+Path(target).name);continue
                        content=archive.read(target)
                        name=f"static/import-{hashlib.sha256(raw).hexdigest()[:12]}-{len(assets)+1}{suffix_image}"
                        assets.append(asset(name,content));image_refs[target]=name
            if len(assets)>100 or sum(item['size'] for item in assets)>10*1024*1024: raise ValueError('图片数量或总大小超出限制')
            def text(node):
                return ''.join(t.text or '' for t in node.findall('.//w:t', NS))
            def images(node):
                values=[]
                for child in node.iter():
                    rid=child.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed')
                    name=image_refs.get(relationships.get(rid))
                    if name: values.append(f'![文档图片]({name})')
                return '\n\n'.join(values)
            parts = []
            for node in tree.find('w:body', NS):
                if node.tag.endswith('}p'):
                    value = text(node)
                    style = node.find('w:pPr/w:pStyle', NS)
                    if style is not None:
                        kind = style.get('{'+NS['w']+'}val', '').lower()
                        if kind.startswith('heading') and kind[-1:] in '123456':
                            value = '#' * int(kind[-1]) + ' ' + value
                    parts.append(value)
                    if images(node): parts.append(images(node))
                elif node.tag.endswith('}tbl'):
                    rows = [[text(cell).replace('|', '\\|').replace('\n', ' ') for cell in row.findall('w:tc', NS)]
                            for row in node.findall('w:tr', NS)]
                    if rows:
                        width = max(map(len, rows))
                        lines = ['| ' + ' | '.join(row + ['']*(width-len(row))) + ' |' for row in rows]
                        lines.insert(1, '| ' + ' | '.join(['---']*width) + ' |')
                        parts.append('\n'.join(lines))
                        if images(node): parts.append(images(node))
            body = '\n\n'.join(parts)
        warnings.append(f'DOCX 已提取段落、标题、基础表格与 {len(assets)} 张图片；批注、页眉页脚和复杂版式请对照原文件。')
    else:
        raise ValueError('仅支持 .md、.pdf、.docx 和 Markdown 附件 .zip 包')
    if not body.strip() or '\x00' in body:
        raise ValueError('文件没有可用正文或包含无效字符')
    if len(body) > MAX_TEXT:
        raise ValueError('提取正文超过 100,000 字符，请拆分文件')
    return {'body': body, 'warnings': warnings, 'assets':assets}


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (90, 90) if '--ocr' in sys.argv else (20, 20))
        if sys.platform != 'darwin':
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        result = extract(sys.stdin.buffer.read(10 * 1024 * 1024 + 1), sys.argv[1],ocr='--ocr' in sys.argv, pdf_note='--pdf-note' in sys.argv)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else '文档解析失败，请检查格式、加密或文件完整性'
        print(json.dumps({'error': message[:240]}, ensure_ascii=False))
