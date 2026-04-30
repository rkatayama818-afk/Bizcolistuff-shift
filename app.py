import streamlit as st
import pandas as pd
import datetime
import json
import os
from io import BytesIO
from solver import solve_shift

# Constants
REQUESTS_FILE = "requests.json"
CONTRACTS_FILE = "current_contracts.csv"

st.set_page_config(page_title="シフト自動生成アプリ", layout="wide")

# Helper to load requests
def load_requests():
    if os.path.exists(REQUESTS_FILE):
        with open(REQUESTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_requests(data):
    with open(REQUESTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

st.title("📅 シフト自動生成アプリ")

# Mode Selection
app_mode = st.sidebar.radio("モード選択", ["スタッフ用（希望休の入力）", "管理者用（シフトの生成）"])

st.sidebar.markdown("---")
st.sidebar.header("カレンダー設定")
current_year = datetime.datetime.now().year
current_month = datetime.datetime.now().month

year = st.sidebar.number_input("対象の年", min_value=2020, max_value=2100, value=current_year, step=1)
month = st.sidebar.number_input("対象の月", min_value=1, max_value=12, value=(current_month%12)+1, step=1)

# Calculate days in month
if month == 12:
    next_month = datetime.date(year + 1, 1, 1)
else:
    next_month = datetime.date(year, month + 1, 1)
days_in_month = (next_month - datetime.timedelta(days=1)).day
days_list = list(range(1, days_in_month + 1))


if app_mode == "スタッフ用（希望休の入力）":
    st.header("🏖️ 希望休の入力")
    
    if not os.path.exists(CONTRACTS_FILE):
        st.warning("管理者がまだ今月の契約条件データをアップロードしていません。店長にお問い合わせください。")
    else:
        df = pd.read_csv(CONTRACTS_FILE, encoding='utf-8')
        staff_names = df['従業員名'].tolist()
        
        st.write(f"**{year}年{month}月** の希望休を入力してください。")
        
        selected_name = st.selectbox("あなたの名前を選んでください", ["-- 選択してください --"] + staff_names)
        
        if selected_name != "-- 選択してください --":
            requests_data = load_requests()
            current_requests = requests_data.get(selected_name, [])
            
            selected_days = st.multiselect(
                "希望休の日付を選んでください",
                options=days_list,
                default=[d for d in current_requests if d in days_list],
                format_func=lambda x: f"{month}/{x}"
            )
            
            if st.button("送信する", type="primary"):
                requests_data[selected_name] = selected_days
                save_requests(requests_data)
                st.success(f"{selected_name} さんの希望休を保存しました！")

elif app_mode == "管理者用（シフトの生成）":
    st.sidebar.markdown("---")
    st.sidebar.header("休館日設定")
    holidays_str = st.sidebar.text_input("休館日の日にち (例: 3,4,5)", "3,4,5,6,10,17,24,31")
    holidays_list = []
    if holidays_str:
        try:
            holidays_list = [int(x.strip()) for x in holidays_str.split(',') if x.strip()]
        except:
            st.sidebar.error("数字とカンマだけで入力してください")

    st.header("⚙️ シフトの生成")
    st.markdown("### 1. 契約条件データのアップロード")
    
    uploaded_file = st.file_uploader("CSVファイルを選択", type=['csv'])
    
    # Process uploaded file
    if uploaded_file is not None:
        try:
            try:
                contracts_df = pd.read_csv(uploaded_file, encoding='shift_jis')
            except:
                contracts_df = pd.read_csv(uploaded_file, encoding='utf-8')
            
            # Save for staff mode
            contracts_df.to_csv(CONTRACTS_FILE, index=False, encoding='utf-8')
            st.success("CSVを読み込み、スタッフ用画面に名簿を反映しました。")
            
        except Exception as e:
            st.error(f"ファイルの読み込みエラー: {e}")
            contracts_df = None
    else:
        # Load existing if available
        if os.path.exists(CONTRACTS_FILE):
            contracts_df = pd.read_csv(CONTRACTS_FILE, encoding='utf-8')
            st.info("前回アップロードされた契約条件データを使用します。（新しいファイルをアップロードして上書きできます）")
        else:
            contracts_df = None

    if contracts_df is not None:
        st.markdown("### 2. 現在集まっている希望休")
        requests_data = load_requests()
        if requests_data:
            req_df = pd.DataFrame([{"名前": k, "希望休日": ", ".join(map(str, sorted(v)))} for k, v in requests_data.items() if v])
            if not req_df.empty:
                st.table(req_df)
            else:
                st.write("まだ誰からも希望休が提出されていません。")
        else:
            st.write("まだ誰からも希望休が提出されていません。")
            
        if st.button("✨ シフトを自動生成する", type="primary"):
            with st.spinner("最適化エンジンがシフトを計算中です..."):
                success, df_output, output_filename = solve_shift(contracts_df, year, month, holidays_list, requests_data)
                
                if success:
                    st.success("✅ シフトの生成に成功しました！")
                    st.dataframe(df_output)
                    
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df_output.to_excel(writer, sheet_name='シフト表')
                    
                    st.download_button(
                        label="📥 Excelファイルをダウンロード",
                        data=output.getvalue(),
                        file_name=output_filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.error("❌ 条件が厳しすぎて解が見つかりませんでした。制約や希望休を緩和してください。")
