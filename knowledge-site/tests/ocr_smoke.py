"""Opt-in local OCR smoke test; creates a synthetic scanned PDF, never reads user files."""
import io
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from PIL import Image,ImageDraw,ImageFont
from document_parser import extract


def main():
    image=Image.new('RGB',(1400,500),'white');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',52)
    draw.text((80,100),'WIKITIDE OCR TEST 2026',font=font,fill='black')
    draw.text((80,220),'Offline document extraction',font=font,fill='black')
    raw=io.BytesIO();image.save(raw,format='PDF',resolution=120)
    result=extract(raw.getvalue(),'.pdf',ocr=True)
    assert 'WIKITIDE' in result['body'].upper(),result['body']
    assert 'EXTRACTION' in result['body'].upper(),result['body']
    print('Synthetic scanned PDF: real local OCR passed (two text lines).')


if __name__=='__main__':main()
