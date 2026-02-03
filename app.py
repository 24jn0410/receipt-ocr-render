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
# API KEY (OCR.space)
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

# 文字列のクリーニング（◎や軽を削除）
def clean_text(text):
    if not text: return ""
    # ◎, 軽, ※, ¥, ￥, カンマを削除
    text = re.sub(r'[◎軽※¥￥,]', '', text)
    # 先頭にある数字（JANコードなど）を削除 (例: 4902... 商品名)
    text = re.sub(r'^\d{8,}\s*', '', text)
    return text.strip()

# 価格抽出（数字だけを取り出す）
def extract_price(text):
    # 「軽」や「¥」を消してから数字を探す
    clean = re.sub(r'[軽\s¥￥,]', '', text)
    match = re.search(r'(\d+)', clean)
    if match:
        return int(match.group(1))
    return None

def parse_receipt_familymart(ocr_text):
    if not ocr_text: return [], 0
    
    lines = ocr_text.splitlines()
    items = []
    total_amount = 0
    
    # 1. 解析エリアの限定
    # 「領収証」から「合計」の間にある行だけが商品の候補
    start_keywords = ['領', '収', '証']
    end_keywords = ['合', '計']
    
    start_index = -1
    end_index = -1

    # 開始位置を探す
    for i, line in enumerate(lines):
        if any(k in line for k in start_keywords) and '登録番号' not in line:
            start_index = i
            break
    
    # 終了位置（合計）を探す
    for i, line in enumerate(lines):
        if i > start_index and any(k in line for k in end_keywords):
            # 合計行から金額を抽出しておく
            p = extract_price(line)
            if p: total_amount = p
            end_index = i
            break
            
    # もし「領収証」が見つからなければ、最初から「合計」までを見る
    if start_index == -1: start_index = 0
    if end_index == -1: end_index = len(lines)

    # 2. 候補行の抽出
    body_lines = lines[start_index+1 : end_index]
    
    candidate_names = []
    candidate_prices = []

    # 無視するキーワード（ノイズ除去）
    ignore_words = [
        '電話', 'TEL', '日付', 'Date', 'No.', 'レジ', '責', 
        '対象', '消費税', '税', '点', 'お預り', '釣', '支払', 
        '割引', '値引', 'クーポン', '小計'
    ]

    for line in body_lines:
        line = line.strip()
        if not line: continue
        
        # 無視キーワードが含まれていたらスキップ
        if any(w in line for w in ignore_words): continue
        if '2024年' in line or '2025年' in line: continue # 日付スキップ

        # 行の特徴を分析
        price_val = extract_price(line)
        cleaned_str = clean_text(line)

        # パターンA: 同じ行に「商品名」と「価格」がある場合 (例: チョコパン ¥168)
        # 正規表現: (文字) (スペース) (円マークor数字)
        match_inline = re.search(r'^(.+?)\s+[¥￥]?\s*([0-9,]+)[軽]?$', line)
        if match_inline:
            name_part = clean_text(match_inline.group(1))
            price_part = extract_price(match_inline.group(2))
            if name_part and price_part and 10 <= price_part <= 100000:
                items.append({'name': name_part, 'price': price_part})
                continue # この行は処理完了

        # パターンB: バラバラの場合のリスト作成
        # 価格っぽい行（¥が含まれる、または末尾が「軽」）
        is_price_line = ('¥' in line or '￥' in line or line.endswith('軽')) and price_val is not None
        
        if is_price_line:
            candidate_prices.append(price_val)
        else:
            # 価格っぽくないなら商品名の候補
            # 数字だけの行は商品名じゃない
            if not cleaned_str.isdigit() and len(cleaned_str) > 1:
                candidate_names.append(cleaned_str)

    # 3. パターンBのマッチング (Zip)
    # すでにインラインで見つかったアイテム以外に、バラバラのやつがあれば結合
    # リストの長さが同じ、もしくは価格リストの方が少ない場合（商品名が改行されている可能性）
    
    # 既存のitemsに追加する形で結合
    if len(candidate_names) > 0 and len(candidate_prices) > 0:
        # 価格の数に合わせて後ろからマッチングさせる（レシートは上から順だが、OCRのズレを考慮）
        # 簡単のため、上から順にペアにする戦略をとる
        count = min(len(candidate_names), len(candidate_prices))
        for i in range(count):
            name = candidate_names[i]
            price = candidate_prices[i]
            # 極端な価格を除外
            if 10 <= price <= 100000:
                items.append({'name': name, 'price': price})

    # 合計金額が0の場合、itemsの合計を計算
    if total_amount == 0:
        total_amount = sum(item['price'] for item in items)

    return items, total_amount

def call_ocr_api(filepath):
    url = 'https://api.ocr.space/parse/image'
    payload = {
        'apikey': API_KEY,
        'language': 'jpn',
        'isOverlayRequired': False,
        'OCREngine': 2, # Engine 2 は表形式に強い
        'scale': True
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

                # FamilyMart専用解析ロジック
                items, total = parse_receipt_familymart(raw_text)
                
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
                    'receipt_items': items, # HTML側と合わせた変数名
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
