from flask import Flask, render_template, request, Response
import requests
import os
import re
import csv
import io
import sqlite3
from datetime import datetime

app = Flask(__name__)

# 配置信息
API_KEY = 'K83802931788957'  # 您的 OCR.space API Key
DB_NAME = '/tmp/receipts.db'

def init_db():
    """初始化數據庫"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  filename TEXT, 
                  total_price INTEGER, 
                  upload_time TEXT)''')
    conn.commit()
    conn.close()

init_db()

def clean_item_name(name):
    """嚴格清洗商品名稱，移除 ◎, 輕, ※, 雜訊數字"""
    if not name: return ""
    # 1. 移除開頭的長串雜訊數字 (例如 OCR 誤認的 111213121)
    name = re.sub(r'^\d{5,}', '', name)
    # 2. 移除 ◎, 輕, ※, *, ¥, ￥ 以及空格 (根據老師要求：余計な文字は一切入れてはいけません)
    name = re.sub(r'[◎輕軽※\*¥￥\s]', '', name)
    return name.strip()

def parse_receipt(ocr_text):
    """解析 OCR 文本並配對商品與價格"""
    if not ocr_text: return [], 0
    lines = ocr_text.splitlines()
    
    found_items = []
    total_amount = 0
    pending_name = None

    # 排除清單：不視為商品的關鍵字
    ignore_list = ['電話', 'TEL', '新宿', '登録番号', '2024', '2025', '対象', '消費税', '交通系', '残高', 'カード', '再発行', 'クーポン', '発行店']

    for line in lines:
        line = line.strip()
        if not line or any(ig in line for ig in ignore_list):
            continue

        # A. 識別合計金額
        if '合計' in line or '合 計' in line:
            nums = re.findall(r'\d+', line.replace(",", ""))
            if nums:
                total_amount = int(nums[-1])
            continue

        # B. 識別金額行 (判斷是否包含價格)
        # 匹配邏輯：尋找 ¥ 符號或行末數字
        price_match = re.search(r'[¥￥]?\s*(\d+)[輕軽]?$', line.replace(",", ""))
        # 檢查這行是否「只有」金額
        is_only_price = re.fullmatch(r'[¥￥]?\s*(\d+)[輕軽]?', line.replace(",", "").strip())

        if price_match:
            price = int(price_match.group(1))
            
            # 如果之前有存好的商品名，則配對
            if (is_only_price or '¥' in line or '￥' in line) and pending_name:
                found_items.append({'name': pending_name, 'price': price})
                pending_name = None
            else:
                # 嘗試在同一行分割名字和價格
                parts = re.split(r'\s+[¥￥]?|(?<=[^\d])(?=[¥￥\d])', line)
                if len(parts) >= 2:
                    name_part = clean_item_name(parts[0])
                    if name_part and not name_part.isdigit():
                        found_items.append({'name': name_part, 'price': price})
                        pending_name = None
        else:
            # C. 沒有金額，視為潛在商品名暫存
            cleaned = clean_item_name(line)
            # 排除掉太短、純數字或收據標頭
            if cleaned and not cleaned.isdigit() and len(cleaned) >= 2:
                if 'Family' not in cleaned and '領収' not in cleaned:
                    pending_name = cleaned

    # 如果沒抓到合計行，則手動加總
    if total_amount == 0 and found_items:
        total_amount = sum(item['price'] for item in found_items)

    return found_items, total_amount

@app.route('/', methods=['GET', 'POST'])
def index():
    data = {}
    if request.method == 'POST':
        file = request.files.get('file')
        if file and file.filename != '':
            filepath = os.path.join('/tmp', file.filename)
            file.save(filepath)
            try:
                # 調用 OCR 服務
                res = requests.post('https://api.ocr.space/parse/image', 
                                    files={'file': open(filepath, 'rb')}, 
                                    data={'apikey': API_KEY, 'language': 'jpn', 'OCREngine': 2, 'scale': True}).json()
                
                raw_text = res['ParsedResults'][0]['ParsedText'] if res.get('ParsedResults') else "OCR failed"
                items, total = parse_receipt(raw_text)
                
                # 存入資料庫
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute("INSERT INTO history (filename, total_price, upload_time) VALUES (?,?,?)", (file.filename, total, str(datetime.now())))
                conn.commit()
                conn.close()

                # 生成 CSV 內容
                csv_out = io.StringIO()
                writer = csv.writer(csv_out)
                writer.writerow(['商品名', '価格'])
                for i in items:
                    writer.writerow([i['name'], i['price']])
                writer.writerow(['合計', total])

                data = {
                    'receipt_list': items, 
                    'total': total, 
                    'raw_text': raw_text, 
                    'csv_data': csv_out.getvalue(), 
                    'log_data': f"Date: {datetime.now()}\nFile: {file.filename}\n\n[Raw Text]\n{raw_text}"
                }
            except Exception as e:
                return f"Error: {str(e)}"
            finally:
                if os.path.exists(filepath): os.remove(filepath)
            return render_template('index.html', data=data)
    return render_template('index.html', data=None)

@app.route('/download_csv', methods=['POST'])
def download_csv():
    return Response(request.form.get('csv_content'), mimetype="text/csv", headers={"Content-disposition": "attachment; filename=receipt.csv"})

@app.route('/download_log', methods=['POST'])
def download_log():
    return Response(request.form.get('log_content'), mimetype="text/plain", headers={"Content-disposition": "attachment; filename=ocr.log"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
