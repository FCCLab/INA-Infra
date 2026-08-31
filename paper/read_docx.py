import zipfile
import xml.etree.ElementTree as ET
import sys
from pathlib import Path

docx_path = Path(__file__).parent / 'Experiment Set 1.docx'
try:
    with zipfile.ZipFile(docx_path) as z:
        xml_content = z.read('word/document.xml')
        tree = ET.fromstring(xml_content)
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        body = tree.find('w:body', ns)
        if body is None:
            print("No body found")
            sys.exit(0)
            
        for elem in body:
            tag = elem.tag.split('}')[-1]
            if tag == 'p':
                text = ''.join(node.text for node in elem.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t') if node.text)
                if text.strip():
                    print(f"[P] {text}")
            elif tag == 'tbl':
                print("\n[TABLE]")
                for tr in elem.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr'):
                    row = []
                    for tc in tr.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc'):
                        tc_text = ''.join(node.text for node in tc.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t') if node.text)
                        row.append(tc_text.strip())
                    print(" | ".join(row))
                print("[/TABLE]\n")
except Exception as e:
    print(f"Error: {e}")
