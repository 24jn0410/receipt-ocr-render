from flask import Flask, render_template, request, Response
import requests
import os
import re
import csv
import io
import sqlite3
from datetime import datetime

app = Flask(__name__)

# OCR.space API KEY
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
    # ◎, 軽, ※, ¥, ￥, カンマを削除
    text = re.sub(r'[◎軽※¥￥,]', '', text)
    # 先頭の長い数字（JANコード等）を削除
    text = re.sub(r'^\d{8,}\s*', '', text)
    return text.strip()

def extract_price(text):
    # 「軽」や「¥」を消してから数字を探す
    clean = re.sub(r'[軽\s¥￥,]', '', text)
    match = re.search(r'(\d+)', clean)
    if match:
        return int(match.group(1))
    return None

def parse_receipt_universal(ocr_text):
    if not ocr_text: return [], 0
    
    lines = ocr_text.splitlines()
    items = []
    total_amount = 0
    
    # 解析エリアの限定（ヘッダーとフッターのノイズ除去）
    start_index = 0
    end_index = len(lines)
    
    for i, line in enumerate(lines):
        if '領収' in line or '電話' in line or 'TEL' in line:
            start_index = i
        if '合' in line and '計' in line:
            end_index = i + 1 # 合計行まで含む
            # ここで合計金額取得トライ
            p = extract_price(line)
            if p: total_amount = p

    # 解析対象の行
    body_lines = lines[start_index:end_index]
    
    candidate_names = []
    candidate_prices = []

    ignore_words = ['電話', 'TEL', '日付', 'Date', 'No.', 'レジ', '責', '対象', '消費税', '税', 'お預り', '釣', '支払', '割引', '値引', 'クーポン', '店', '会員']

    for line in body_lines:
        line = line.strip()
        if not line: continue
        if any(w in line for w in ignore_words): continue
        if '2024' in line or '2025' in line: continue

        cleaned_str = clean_text(line)
        price_val = extract_price(line)

        # パターンA：1行に「商品名」と「価格」がある (例: チョコ ¥100)
        # 条件: 文字列 + スペース + 数字
        match_inline = re.search(r'^(.+?)\s+[¥￥]?\s*([0-9,]+)[軽]?$', line)
        
        if match_inline:
            name_part = clean_text(match_inline.group(1))
            price_part = extract_price(match_inline.group(2))
            
            # 名前が数字だけじゃない、かつ価格が正常範囲
            if name_part and not name_part.isdigit() and price_part and 10 <= price_part <= 100000:
                items.append({'name': name_part, 'price': price_part})
                continue # 処理済み

        # パターンB：分離型（名前だけの行、価格だけの行）
        # 価格っぽい行かどうか判定
        is_price_line = False
        if price_val is not None:
            # 「¥」がある、または「軽」がある、または行のほとんどが数字
            if '¥' in line or '￥' in line or '軽' in line:
                is_price_line = True
            elif len(line) < 10 and re.match(r'^\s*[0-9,]+\s*$', line):
                is_price_line = True

        if is_price_line:
            candidate_prices.append(price_val)
        else:
            # 名前候補（数字だけ、短すぎるものは除外）
            if len(cleaned_str) > 1 and not cleaned_str.isdigit():
                candidate_names.append(cleaned_str)

    # ジッパー機能：名前リストと価格リストを結合
    # Inlineで取れなかった場合のみ発動
    if len(candidate_names) > 0 and len(candidate_prices) > 0:
        # 数が合わない場合、少ない方に合わせる
        count = min(len(candidate_names), len(candidate_prices))
        for i in range(count):
            # レシート2のような「上から順」に対応
            name = candidate_names[i]
            price = candidate_prices[i]
            
            # 重複防止：すでにInlineで追加されたものと被っていないかチェック（簡易）
            if not any(item['price'] == price for item in items):
                 if 10 <= price <= 100000:
                    items.append({'name': name, 'price': price})

    # 合計金額の最終確認
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

                items, total = parse_receipt_universal(raw_text)
                save_to_db(file.filename, total)
                
                # CSV生成
                csv_output = io.StringIO()
                writer = csv.writer(csv_output)
                writer.writerow(['商品名', '価格'])
                for item in items:
                    writer.writerow([item['name'], item['price']])
                writer.writerow(['合計', total])
                
                # ログ生成
                log_content = f"Date: {datetime.now()}\nFile: {file.filename}\n\n[Raw Text]\n{raw_text}\n"

                # 【重要】ここで変数名を 'items' に統一しています！
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
                return f"Internal Error: {e}"
            finally:
                if os.path.exists(filepath): os.remove(filepath)
            
            return render_template('index.html', data=data)

    return render_template('index.html', data=None)

@app.route('/download_csv', methods=['POST'])
def download_csv():
    return Response(request.form.get('csv_content', ''), mimetype="text/csv", 
                   headers={"Content-disposition": "attachment; filename=receipt.csv"})

@app.route('/download_log', methods=['POST'])
def download_log():
    return Response(request.form.get('log_content', ''), mimetype="text/plain", 
                   headers={"Content-disposition": "attachment; filename=ocr.log"})

if __name__ == '__main__':
    app.run(debug=True)
