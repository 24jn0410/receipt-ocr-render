from flask import Flask, render_template, request, Response
import requests
import os
import re
import csv
import io
import sqlite3
from datetime import datetime

app = Flask(__name__)

API_KEY = 'K83802931788957'
DB_NAME = '/tmp/receipts.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, total_price INTEGER, upload_time TEXT)''')
    conn.commit()
    conn.close()

init_db()

def clean_item_name(name):
    if not name: return ""
    # 移除 OCR 誤認的長串數字、◎、輕、※、空格及貨幣符號
    name = re.sub(r'^\d{5,}', '', name)
    name = re.sub(r'[◎輕軽※\*¥￥\s]', '', name)
    return name.strip()

def parse_receipt(ocr_text):
    if not ocr_text: return [], 0
    lines = ocr_text.splitlines()
    found_items = []
    total_amount = 0
    pending_name = None

    # 強力排除清單：新增對 レジ, 責No, No., 4-, ( 等噪點的排除
    ignore_patterns = [
        r'電話', r'TEL', r'新宿', r'登録番号', r'202\d', r'対象', r'消費税', 
        r'交通系', r'残高', r'カード', r'クーポン', r'発行店', r'責No', r'レジ', 
        r'No\.', r'\d+-\d+', r'^\d+$', r'^\($', r'キリトリ', r'領収証'
    ]

    for line in lines:
        line = line.strip()
        if not line: continue
        # 如果行內包含任何排除模式，直接跳過
        if any(re.search(p, line) for p in ignore_patterns): continue

        # A. 合計金額識別 (抓取最後一組數字)
        if '合計' in line.replace(" ", ""):
            nums = re.findall(r'\d+', line.replace(",", ""))
            if nums: total_amount = int(nums[-1])
            continue

        # B. 價格與商品匹配
        # 改進：要求價格前必須有 ¥ 或空格，且價格通常大於 10
        price_match = re.search(r'[¥￥]\s*(\d+)', line.replace(",", ""))
        
        if price_match:
            price = int(price_match.group(1))
            # 排除掉太小的數字（如 8% 等誤報）
            if price < 10: continue

            # 嘗試抓取同一行的名字
            name_part = line.split('¥')[0].split('￥')[0].strip()
            # 如果名字部分包含 레지 等雜訊，則捨棄
            if any(re.search(p, name_part) for p in ignore_patterns): continue
            
            cleaned_name = clean_item_name(name_part)
            
            if cleaned_name and not cleaned_name.isdigit():
                found_items.append({'name': cleaned_name, 'price': price})
                pending_name = None
            elif pending_name:
                found_items.append({'name': pending_name, 'price': price})
                pending_name = None
        else:
            # C. 暫存可能的商品名
            cleaned = clean_item_name(line)
            # 名稱必須長度大於 2，且不包含特殊字元
            if cleaned and not cleaned.isdigit() and len(cleaned) >= 2:
                pending_name = cleaned

    # 如果沒抓到合計，且有商品，手動加總
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
                res = requests.post('https://api.ocr.space/parse/image', 
                                    files={'file': open(filepath, 'rb')}, 
                                    data={'apikey': API_KEY, 'language': 'jpn', 'OCREngine': 2, 'scale': True}).json()
                
                raw_text = res['ParsedResults'][0]['ParsedText'] if res.get('ParsedResults') else ""
                items, total = parse_receipt(raw_text)
                
                # DB 保存
                conn = sqlite3.connect(DB_NAME); c = conn.cursor()
                c.execute("INSERT INTO history (filename, total_price, upload_time) VALUES (?,?,?)", (file.filename, total, str(datetime.now()))); conn.commit(); conn.close()

                # CSV
                csv_out = io.StringIO(); writer = csv.writer(csv_out)
                writer.writerow(['商品名', '価格'])
                for i in items: writer.writerow([i['name'], i['price']])
                writer.writerow(['合計', total])

                data = {'receipt_list': items, 'total': total, 'raw_text': raw_text, 'csv_data': csv_out.getvalue(), 'log_data': f"File: {file.filename}\n{raw_text}"}
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
    app.run(debug=True)
