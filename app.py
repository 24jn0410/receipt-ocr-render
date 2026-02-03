from flask import Flask, render_template, request, send_file, Response
import requests
import os
import re
import csv
import io
import sqlite3
import json
from datetime import datetime

app = Flask(__name__)

# ==========================================
# API KEY 設定
API_KEY = 'K83802931788957' 
# ==========================================

# 資料庫路徑 (Render 使用 /tmp)
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
    text = re.sub(r'[◎軽]', '', text)
    return text.strip()

def parse_receipt(ocr_text):
    if not ocr_text: return [], 0
    
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

def call_ocr_api(filepath):
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'apikey': API_KEY,
        'language': 'jpn',
        'isOverlayRequired': False,
        'OCREngine': 2
    }
    with open(filepath, 'rb') as f:
        r = requests.post(url, files={'file': f}, data=payload)
    return r.json()

@app.route('/', methods=['GET', 'POST'])
def index():
    data = {}
    
    if request.method == 'POST':
        if 'file' not in request.files: return "No file uploaded"
        file = request.files['file']
        if file.filename == '': return "No file selected"
        
        if file:
            filepath = os.path.join('/tmp', file.filename)
            file.save(filepath)
            
            try:
                print(f"Processing: {file.filename}")
                json_result = call_ocr_api(filepath)
                
                raw_text = ""
                if json_result.get('OCRExitCode') == 1 and json_result.get('ParsedResults'):
                    raw_text = json_result['ParsedResults'][0]['ParsedText']
                else:
                    error_msg = json_result.get('ErrorMessage') or str(json_result)
                    return f"<h1>OCR API Error</h1><p>{error_msg}</p>"

                items, total = parse_receipt(raw_text)
                save_to_db(file.filename, total)
                
                log_content = f"--- Processed at {datetime.now()} ---\n"
                log_content += f"File: {file.filename}\n"
                log_content += f"Raw Text:\n{raw_text}\n"
                
                csv_output = io.StringIO()
                writer = csv.writer(csv_output)
                writer.writerow(['商品名', '価格'])
                for item in items:
                    writer.writerow([item['name'], item['price']])
                writer.writerow(['合計', total])
                csv_string = csv_output.getvalue()

                # 【修正點】這裡使用了新的變數名 receipt_items
                data = {
                    'receipt_items': items,
                    'total': total,
                    'raw_text': raw_text,
                    'csv_data': csv_string,
                    'log_data': log_content
                }

            except Exception as e:
                import traceback
                traceback.print_exc()
                return f"<h1>Server Error</h1><p>{str(e)}</p>"
            
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)

            return render_template('index.html', data=data)

    return render_template('index.html', data=None)

@app.route('/download_csv', methods=['POST'])
def download_csv():
    csv_content = request.form.get('csv_content', '')
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=receipt.csv"}
    )

@app.route('/download_log', methods=['POST'])
def download_log():
    log_content = request.form.get('log_content', '')
    return Response(
        log_content,
        mimetype="text/plain",
        headers={"Content-disposition": "attachment; filename=ocr.log"}
    )

if __name__ == '__main__':
    app.run(debug=True)
