from flask import Flask, render_template, request, send_file, Response
import requests
import os
import re
import csv
import io
import sqlite3
from datetime import datetime

app = Flask(__name__)

# ==========================================
# 【重要】請填入你的 OCR.space Key
API_KEY = 'K83802931788957' 
# ==========================================

DB_NAME = 'receipts.db'

def init_db():
    """初始化資料庫"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # 建立一個簡單的表來存儲紀錄
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  filename TEXT, 
                  total_price INTEGER, 
                  upload_time TEXT)''')
    conn.commit()
    conn.close()

# 啟動時初始化 DB
init_db()

def save_to_db(filename, total):
    """寫入資料庫"""
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO history (filename, total_price, upload_time) VALUES (?, ?, ?)",
                  (filename, total, str(datetime.now())))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")

def clean_text(text):
    text = re.sub(r'[◎軽]', '', text)
    return text.strip()

def parse_receipt(ocr_text):
    lines = ocr_text.split('\r\n')
    items = []
    total_amount = 0
    price_pattern = re.compile(r'[¥￥]?\s*([0-9,]+)\s*$')

    for line in lines:
        line = line.strip()
        if not line: continue

        if "合　計" in line or "合計" in line: continue
        if "釣" in line or "預" in line: continue

        match = price_pattern.search(line)
        if match:
            price_str = match.group(1).replace(',', '')
            try:
                price = int(price_str)
                product_name = line[:match.start()].strip()
                product_name = clean_text(product_name)
                
                if len(product_name) > 0:
                    items.append({'name': product_name, 'price': price})
                    total_amount += price
            except:
                pass
    
    return items, total_amount

def call_ocr_api(filename):
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'apikey': API_KEY,
        'language': 'jpn',
        'isOverlayRequired': False,
        'OCREngine': 2
    }
    with open(filename, 'rb') as f:
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
            file.save(file.filename)
            json_result = call_ocr_api(file.filename)
            
            raw_text = ""
            if json_result.get('OCRExitCode') == 1:
                raw_text = json_result['ParsedResults'][0]['ParsedText']
            else:
                raw_text = "Error: " + str(json_result.get('ErrorMessage'))
            
            items, total = parse_receipt(raw_text)
            
            # === 儲存到資料庫 ===
            save_to_db(file.filename, total)
            
            # 生成 Log
            log_content = f"--- Processed at {datetime.now()} ---\n"
            log_content += f"File: {file.filename}\n"
            log_content += f"Raw Text:\n{raw_text}\n"
            log_content += f"Extracted Items: {items}\n"
            log_content += f"Calculated Total: {total}\n"
            
            # 生成 CSV
            csv_output = io.StringIO()
            writer = csv.writer(csv_output)
            writer.writerow(['商品名', '価格'])
            for item in items:
                writer.writerow([item['name'], item['price']])
            writer.writerow(['合計', total])
            csv_string = csv_output.getvalue()

            data = {
                'items': items,
                'total': total,
                'raw_text': raw_text,
                'csv_data': csv_string,
                'log_data': log_content
            }

            try:
                os.remove(file.filename)
            except:
                pass

            return render_template('index.html', data=data)

    return render_template('index.html', data=None)

@app.route('/download_csv', methods=['POST'])
def download_csv():
    csv_content = request.form['csv_content']
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=receipt.csv"}
    )

@app.route('/download_log', methods=['POST'])
def download_log():
    log_content = request.form['log_content']
    return Response(
        log_content,
        mimetype="text/plain",
        headers={"Content-disposition": "attachment; filename=ocr.log"}
    )

if __name__ == '__main__':
    # Azure VM 需要監聽 0.0.0.0 port 80
    app.run(host='0.0.0.0', port=80)