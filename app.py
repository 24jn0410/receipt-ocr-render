from flask import Flask, render_template, request, Response
import requests
import os
import re
import csv
import io
import sqlite3
from datetime import datetime

app = Flask(__name__)

# ==========================================
# OCR.space API KEY
API_KEY = 'K83802931788957'
# ==========================================

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
    text = re.sub(r'[◎軽※¥￥,]', '', text)
    text = re.sub(r'^\d{4,}\s*', '', text)
    return text.strip()

def extract_price(text):
    if not text: return None
    clean = re.sub(r'[軽\s¥￥,]', '', text)
    match = re.search(r'(\d+)', clean)
    if match:
        return int(match.group(1))
    return None

def parse_receipt_hybrid(ocr_text):
    if not ocr_text: return [], 0
    lines = ocr_text.splitlines()
    items = []
    total_amount = 0
    
    ignore_words = [
        '電話', 'TEL', '日付', 'Date', 'No.', 'レジ', '責', '領', '収', '証',
        '対象', '消費税', '税', 'お預り', '釣', '支払', '割引', '値引', 'クーポン', 
        '店', '会員', 'ポイント', '時刻', 'カード', 'QR', 'アプリ', '番号', '登録'
    ]

    candidate_names = []
    candidate_prices = []

    for line in lines:
        line = line.strip()
        if not line: continue
        
        if '合' in line and '計' in line:
            p = extract_price(line)
            if p: total_amount = p
            continue 

        if any(w in line for w in ignore_words): continue
        if '2024' in line or '2025' in line: continue

        # Logic A: Inline
        match_inline = re.search(r'^(.+?)\s+[¥￥]?\s*([0-9,]+)[軽]?$', line)
        is_matched = False
        if match_inline:
            name_part = clean_text(match_inline.group(1))
            price_part = extract_price(match_inline.group(2))
            if name_part and not name_part.isdigit() and price_part and 10 <= price_part <= 100000:
                items.append({'name': name_part, 'price': price_part})
                is_matched = True

        # Logic B: Zipper
        if not is_matched:
            price_val = extract_price(line)
            clean_str = clean_text(line)
            is_price_line = False
            if '¥' in line or '￥' in line or '軽' in line:
                is_price_line = True
            elif re.match(r'^\s*[0-9,]+\s*$', line):
                is_price_line = True
            
            if is_price_line and price_val is not None:
                candidate_prices.append(price_val)
            elif len(clean_str) > 1 and not clean_str.isdigit():
                candidate_names.append(clean_str)

    if len(candidate_names) > 0 and len(candidate_prices) > 0:
        count = min(len(candidate_names), len(candidate_prices))
        for i in range(count):
            name = candidate_names[i]
            price = candidate_prices[i]
            if 10 <= price <= 100000:
                items.append({'name': name, 'price': price})

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
                print(f"Processing: {file.filename}")
                json_result = call_ocr_api(filepath)
                raw_text = ""
                if json_result.get('OCRExitCode') == 1 and json_result.get('ParsedResults'):
                    raw_text = json_result['ParsedResults'][0]['ParsedText']
                else:
                    raw_text = "Error: " + str(json_result.get('ErrorMessage'))

                items, total = parse_receipt_hybrid(raw_text)
                save_to_db(file.filename, total)
                
                csv_output = io.StringIO()
                writer = csv.writer(csv_output)
                writer.writerow(['商品名', '価格'])
                for item in items:
                    writer.writerow([item['name'], item['price']])
                writer.writerow(['合計', total])
                
                log_content = f"Date: {datetime.now()}\nFile: {file.filename}\n\n[Raw Text]\n{raw_text}\n"

                # 【重要】ここを変数名 'receipt_items' に変更しました！
                data = {
                    'receipt_items': items,
                    'total': total,
                    'raw_text': raw_text,
                    'csv_data': csv_output.getvalue(),
                    'log_data': log_content
                }

            except Exception as e:
                import traceback
                print(traceback.format_exc())
                return f"<h1>Internal Error</h1><pre>{str(e)}</pre>"
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
