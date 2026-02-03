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
# 請確認 API KEY 是否有效
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

def clean_name(text):
    # 移除特殊的標記符號，如 ◎, 軽, ※, ¥
    text = re.sub(r'[◎軽※¥￥]', '', text)
    # 移除開頭可能出現的純數字編號 (例如 4902...)
    text = re.sub(r'^\d+\s*', '', text)
    return text.strip()

def parse_receipt(ocr_text):
    if not ocr_text: return [], 0
    
    lines = ocr_text.splitlines()
    items = []
    total_amount = 0
    
    # 1. 忽略列表：只要包含這些字，這行通常不是商品
    ignore_keywords = [
        '電話', 'TEL', '日付', '登録', '番号', 'レジ', '責', '領', '収', '証', 
        '対象', '消費税', 'マネー', '支払', '釣', '預', '現', '合　計', '合計', 
        '時刻', '店', '会員', 'ポイント', 'カード', 'QR', 'アプリ', 'クーポン'
    ]

    for line in lines:
        line = line.strip()
        if not line: continue
        
        # A. 檢查黑名單
        is_ignored = False
        for kw in ignore_keywords:
            if kw in line:
                is_ignored = True
                break
        if is_ignored: continue

        # B. 【寬容版邏輯】
        # 不使用複雜正則，而是直接切分空白
        # 假設：每一行的最後一個區塊是價格，前面全部是商品名
        
        parts = line.split() # 用空白切割
        if len(parts) < 2: continue # 如果這行只有一個東西，肯定不是「商品+價格」

        # 嘗試抓取最後一個部分當作價格
        price_part = parts[-1].replace(',', '').replace('¥', '').replace('￥', '')
        
        # 剩下的前面部分當作名字
        name_part = " ".join(parts[:-1])

        try:
            # 測試最後一部分是不是數字
            price = int(price_part)
            
            # 清理名字
            clean_product_name = clean_name(name_part)
            
            # 名字太短(小於2字)通常是雜訊
            if len(clean_product_name) < 2: continue
            
            # 過濾掉日期 (例如 2024年...)
            if "年" in clean_product_name or "月" in clean_product_name: continue

            # 價格範圍檢查 (1元 ~ 10萬元)
            if 1 <= price <= 100000:
                items.append({'name': clean_product_name, 'price': price})
                total_amount += price
        except:
            # 如果最後一部分不是數字，這行就不是商品
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
                    raw_text = f"Error: {error_msg}"

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
