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
    """清理商品名稱：移除 ◎, 軽, 以及開頭的條碼數字"""
    if not text: return ""
    # 移除特殊符號
    text = re.sub(r'[◎軽※¥￥,]', '', text)
    # 移除開頭像是條碼的長數字 (例如 4902...)
    text = re.sub(r'^\d{4,}\s*', '', text)
    return text.strip()

def extract_price(text):
    """從字串中提取價格數字"""
    if not text: return None
    # 移除 軽, ¥, 空白, 逗號
    clean = re.sub(r'[軽\s¥￥,]', '', text)
    # 尋找數字
    match = re.search(r'(\d+)', clean)
    if match:
        return int(match.group(1))
    return None

def parse_receipt_hybrid(ocr_text):
    if not ocr_text: return [], 0
    
    lines = ocr_text.splitlines()
    items = []
    total_amount = 0
    
    # 1. 黑名單：過濾掉絕對不是商品的行
    ignore_words = [
        '電話', 'TEL', '日付', 'Date', 'No.', 'レジ', '責', '領', '収', '証',
        '対象', '消費税', '税', 'お預り', '釣', '支払', '割引', '値引', 'クーポン', 
        '店', '会員', 'ポイント', '時刻', 'カード', 'QR', 'アプリ', '番号', '登録'
    ]

    # 用來暫存「落單」的商品名和價格
    candidate_names = []
    candidate_prices = []

    for line in lines:
        line = line.strip()
        if not line: continue
        
        # 嘗試抓取合計金額
        if '合' in line and '計' in line:
            p = extract_price(line)
            if p: total_amount = p
            continue # 合計行跳過

        # 檢查黑名單
        if any(w in line for w in ignore_words): continue
        if '2024' in line or '2025' in line: continue

        # --- 邏輯 A：同一行有名字和價格 (Inline) ---
        # 格式：文字 + 空白 + (¥)數字(軽)
        match_inline = re.search(r'^(.+?)\s+[¥￥]?\s*([0-9,]+)[軽]?$', line)
        
        is_matched = False
        if match_inline:
            name_part = clean_text(match_inline.group(1))
            price_part = extract_price(match_inline.group(2))
            
            # 確保名字不是純數字，價格合理
            if name_part and not name_part.isdigit() and price_part and 10 <= price_part <= 100000:
                items.append({'name': name_part, 'price': price_part})
                is_matched = True

        # --- 邏輯 B：分離式 (Zipper) ---
        # 如果邏輯 A 沒抓到，就分別收集名字和價格
        if not is_matched:
            price_val = extract_price(line)
            
            # 判斷這行是不是「價格行」
            # 特徵：包含 ¥ 或 軽，或者是純數字
            is_price_line = False
            if '¥' in line or '￥' in line or '軽' in line:
                is_price_line = True
            elif re.match(r'^\s*[0-9,]+\s*$', line):
                is_price_line = True
            
            if is_price_line and price_val is not None:
                candidate_prices.append(price_val)
            else:
                # 判斷這行是不是「名字行」
                clean_str = clean_text(line)
                # 長度大於1，且不是純數字
                if len(clean_str) > 1 and not clean_str.isdigit():
                    candidate_names.append(clean_str)

    # 2. 合併落單的名字和價格 (Zipper Logic)
    # 取兩者數量的最小值進行配對
    if len(candidate_names) > 0 and len(candidate_prices) > 0:
        count = min(len(candidate_names), len(candidate_prices))
        for i in range(count):
            name = candidate_names[i]
            price = candidate_prices[i]
            
            # 簡單過濾極端價格
            if 10 <= price <= 100000:
                items.append({'name': name, 'price': price})

    # 如果沒抓到合計，就自己加總
    if total_amount == 0 and items:
        total_amount = sum(item['price'] for item in items)

    return items, total_amount

def call_ocr_api(filepath):
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'apikey': API_KEY,
        'language': 'jpn',
        'isOverlayRequired': False,
        'OCREngine': 2, # Engine 2 對表格支援較好
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

                # 使用混合解析邏輯
                items, total = parse_receipt_hybrid(raw_text)
                
                save_to_db(file.filename, total)
                
                # 準備 CSV
                csv_output = io.StringIO()
                writer = csv.writer(csv_output)
                writer.writerow(['商品名', '価格'])
                for item in items:
                    writer.writerow([item['name'], item['price']])
                writer.writerow(['合計', total])
                
                # 準備 Log
                log_content = f"Date: {datetime.now()}\nFile: {file.filename}\n\n[Raw Text]\n{raw_text}\n"

                # 【關鍵】變數名稱統一為 'items'
                data = {
                    'items': items,
                    'total': total,
                    'raw_text': raw_text,
                    'csv_data': csv_output.getvalue(),
                    'log_data': log_content
                }

            except Exception as e:
                import traceback
                traceback.print_exc()
                # 這裡會把錯誤印在網頁上，而不是 500 Error
                return f"<h1>Internal Error (Debug)</h1><pre>{traceback.format_exc()}</pre>"
            
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
