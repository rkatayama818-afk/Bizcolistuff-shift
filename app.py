import streamlit as st
import pandas as pd
import datetime
import json
import os
from io import BytesIO

# 重いライブラリ(solver, ortools)は起動時ではなく実行時にインポートすることで画面表示を高速化します# Constants
REQUESTS_FILE = "requests.json"
CONTRACTS_FILE = "current_contracts.csv"

st.set_page_config(page_title="シフト自動生成アプリ", layout="centered", initial_sidebar_state="collapsed")

# 契約データの読み込みをキャッシュして高速化
@st.cache_data(ttl=300)
def load_contracts_for_staff():
    if os.path.exists(CONTRACTS_FILE):
        return pd.read_csv(CONTRACTS_FILE, encoding='utf-8')
    return None

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
    
    df = load_contracts_for_staff()
    if df is None:
        st.warning("管理者がまだ今月の契約条件データをアップロードしていません。店長にお問い合わせください。")
    else:
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
            load_contracts_for_staff.clear() # キャッシュをクリアして最新を反映
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
        # --- データのプレチェック ---
        st.markdown("### 2. データの読み取りチェック")
        
        # 安全な数値変換
        def safe_numeric(df, col, default=0):
            if col not in df.columns:
                df[col] = default
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(default)

        safe_numeric(contracts_df, '土曜出勤上限', 0)
        safe_numeric(contracts_df, '週勤務上限', 5)
        safe_numeric(contracts_df, '夜勤コアチームフラグ', 0)
        safe_numeric(contracts_df, '日勤専従フラグ', 0)
        safe_numeric(contracts_df, '柔軟シフトフラグ', 0)

        total_sat_capacity = contracts_df['土曜出勤上限'].sum()
        saturdays_count = sum(1 for d in days_list if datetime.date(year, month, d).weekday() == 5)
        required_sat_shifts = saturdays_count * 2
        
        night_core_count = (contracts_df['夜勤コアチームフラグ'] == 1).sum()
        flexible_count = (contracts_df['柔軟シフトフラグ'] == 1).sum()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            if total_sat_capacity >= required_sat_shifts:
                st.success(f"✅ 土曜出勤枠: {int(total_sat_capacity)}枠 (必要: {required_sat_shifts}枠)")
            else:
                st.error(f"❌ 土曜出勤枠不足！: {int(total_sat_capacity)}枠 (必要: {required_sat_shifts}枠)")
        with col2:
            if night_core_count >= 2:
                st.success(f"✅ 夜勤コアチーム: {night_core_count}人")
            else:
                st.error(f"❌ 夜勤コアチーム不足！: {night_core_count}人 (最低2人は必要)")
        with col3:
            st.info(f"ℹ️ 柔軟シフト対応: {flexible_count}人")
            
        if total_sat_capacity < required_sat_shifts or night_core_count < 2:
            st.warning("⚠️ 上記の赤枠の部分が原因でエラー（解なし）になる可能性が非常に高いです。CSVの設定を見直してください。")

        import unicodedata
        weekday_day_capacity = 0
        weekday_night_capacity = 0
        
        night_shifts_base = ['L', 'D', 'E', 'H']
        day_shifts_base = ['N', 'S', 'A', 'B', 'C', 'F', 'G']
        all_shift_types_base = day_shifts_base + night_shifts_base
        
        for e_idx, row in contracts_df.iterrows():
            contract_shift_raw = str(row.get('シフトコード', '')).strip()
            contract_shift = unicodedata.normalize('NFKC', contract_shift_raw).upper()
            
            is_night_core = int(row['夜勤コアチームフラグ']) == 1
            is_flexible = int(row['柔軟シフトフラグ']) == 1
            lim = int(row['週勤務上限'])
            
            if contract_shift not in all_shift_types_base:
                is_flexible = True

            can_day = False
            can_night = False
            
            if is_night_core:
                if is_flexible or contract_shift in night_shifts_base:
                    can_night = True
            else:
                if is_flexible or contract_shift in day_shifts_base:
                    can_day = True
            
            if can_day: weekday_day_capacity += lim
            if can_night: weekday_night_capacity += lim
            
        weekdays_count = sum(1 for d in days_list if datetime.date(year, month, d).weekday() < 5 and d not in holidays_list)
        weeks_approx = weekdays_count / 5.0 if weekdays_count > 0 else 1
        req_night_per_week = (weekdays_count * 2) / weeks_approx if weeks_approx > 0 else 10
        req_day_per_week = (weekdays_count * 6) / weeks_approx if weeks_approx > 0 else 30
        
        st.markdown(f"**【平日シフトの最大キャパシティ確認（週あたり）】**")
        col4, col5 = st.columns(2)
        with col4:
            if weekday_night_capacity >= req_night_per_week:
                st.success(f"✅ 平日夜勤パワー: {weekday_night_capacity}日/週 (必要: 約{int(req_night_per_week)}日/週)")
            else:
                st.error(f"❌ 平日夜勤パワー不足！: {weekday_night_capacity}日/週 (必要: 約{int(req_night_per_week)}日/週)")
        with col5:
            if weekday_day_capacity >= req_day_per_week:
                st.success(f"✅ 平日日勤パワー: {weekday_day_capacity}日/週 (必要: 約{int(req_day_per_week)}日/週)")
            else:
                st.error(f"❌ 平日日勤パワー不足！: {weekday_day_capacity}日/週 (必要: 約{int(req_day_per_week)}日/週)")

        st.markdown("### 3. 現在集まっている希望休")
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
                from solver import solve_shift # 実行時のみロード
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
