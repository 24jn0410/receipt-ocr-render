from flask import Flask, render_template, request, Response
import requests
import os
import re
import csv
import io
import sqlite3
from datetime import datetime

app = Flask(__name__)

# OCR API 配置 (請確保 API_KEY 正確)
API_KEY = 'K83802931788957'
DB_NAME = '/tmp/receipts.db'

def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS history 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                      filename TEXT, 
                      total_price INTEGER, 
                      upload_time TEXT)''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database Init Error: {e}")

init_db()

def save_to_db(filename, total):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO history (filename, total_price, upload_time) VALUES (?, ?, ?)",
                  (filename, total, str(datetime.now())))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database Save Error: {e}")

def clean_text(text):
    if not text: return ""
    # 根據要求：保留 ◎，但移除「軽」、星號等雜質
    text = re.sub(r'[軽※\*]', '', text)
    # 移除行首可能出現的長序號數字（非商品名部分）
    text = re.sub(r'^\d{5,}\s*', '', text)
    return text.strip()

def parse_receipt_safe(ocr_text):
    if not ocr_text: return [], 0
    lines = ocr_text.splitlines()
    items = []
    total_amount = 0
    
    # 關鍵字過濾：排除無關的收據資訊
    ignore = ['電話', 'TEL', '新宿', '登録番号', 'レジ', '責No', '領収証', '対象', '內消費税', '交通系', '残高', 'カード番号', '再発行']

    for line in lines:
        line = line.strip()
        if not line or any(w in line for w in ignore): continue
        if '2024' in line or '2025' in line: continue

        # 1. 識別合計金額
        if '合計' in line.replace(" ", ""):
            # 抓取該行最後的數字
            match_total = re.search(r'(\d+)', line.replace(",", "").replace("¥", "").replace("￥", ""))
            if match_total:
                total_amount = int(match_total.group(1))
            continue

        # 2. 識別商品行 (格式: 商品名 [空格] ¥價格 [輕])
        # 正則解釋：抓取開頭文字，中間有空格或貨幣符號，最後是數字，後方可能有個「軽」
        match_item = re.search(r'^(.+?)\s+[¥￥]?\s*(\d+)\s*[軽]?$', line)
        if match_item:
            name = clean_text(match_item.group(1))
            price = int(match_item.group(2))
            # 排除掉純數字或太短的誤判行
            if name and not name.isdigit() and len(name) > 1:
                items.append({'name': name, 'price': price})

    # 預防機制：如果沒抓到總價，計算商品總和
    if total_amount == 0 and items:
        total_amount = sum(item['price'] for item in items)

    return items, total_amount

def call_ocr_api(filepath):
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'apikey': API_KEY,
        'language': 'jpn',
        'isOverlayRequired': False,
        'OCREngine': 2,
        'scale': True
    }
    with open(filepath, 'rb') as f:
        r = requests.post(url, files={'file': f}, data=payload)
    return r.json()

@app.route('/', methods=['GET', 'POST'])
def index():
    data = {}
    if request.method == 'POST':
        if 'file' not in request.files: return "No file"
        file = request.files['file']
        if file.filename == '': return "No file"
        
        if file:
            filepath = os.path.join('/tmp', file.filename)
            file.save(filepath)
            try:
                json_result = call_ocr_api(filepath)
                raw_text = ""
                if json_result.get('OCRExitCode') == 1 and json_result.get('ParsedResults'):
                    raw_text = json_result['ParsedResults'][0]['ParsedText']
                else:
                    raw_text = "OCR Error: " + str(json_result.get('ErrorMessage'))

                items, total = parse_receipt_safe(raw_text)
                save_to_db(file.filename, total)
                
                # 準備 CSV 內容
                csv_output = io.StringIO()
                writer = csv.writer(csv_output)
                writer.writerow(['商品名', '価格'])
                for item in items:
                    writer.writerow([item['name'], item['price']])
                writer.writerow(['合計', total])
                
                log_content = f"Date: {datetime.now()}\nFile: {file.filename}\n\n[Raw Text]\n{raw_text}\n"

                data = {
                    'receipt_list': items,  
                    'total': total,
                    'raw_text': raw_text,
                    'csv_data': csv_output.getvalue(),
                    'log_data': log_content
                }
            except Exception as e:
                return f"<h1>Error</h1><pre>{str(e)}</pre>"
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            
            return render_template('index.html', data=data)

    return render_template('index.html', data=None)

@app.route('/download_csv', methods=['POST'])
def download_csv():
    csv_content = request.form.get('csv_content', '')
    return Response(csv_content, mimetype="text/csv", 
                    headers={"Content-disposition": "attachment; filename=receipt.csv"})

@app.route('/download_log', methods=['POST'])
def download_log():
    log_content = request.form.get('log_content', '')
    return Response(log_content, mimetype="text/plain", 
                    headers={"Content-disposition": "attachment; filename=ocr.log"})

if __name__ == '__main__':
    app.run(debug=True)
