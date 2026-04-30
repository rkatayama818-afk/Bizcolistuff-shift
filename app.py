import streamlit as st
import pandas as pd
import datetime
from io import BytesIO
from solver import solve_shift

st.set_page_config(page_title="シフト自動生成アプリ", layout="wide")

st.title("📅 シフト自動生成アプリ")

# --- サイドバー設定 ---
st.sidebar.header("カレンダー設定")
current_year = datetime.datetime.now().year
current_month = datetime.datetime.now().month

# 年月の選択
year = st.sidebar.number_input("対象の年", min_value=2020, max_value=2100, value=current_year, step=1)
month = st.sidebar.number_input("対象の月", min_value=1, max_value=12, value=(current_month%12)+1, step=1)

st.sidebar.markdown("---")
st.sidebar.header("休館日設定")
st.sidebar.write("※日曜日や祝日など、全員が休みになる日をカンマ区切りで入力してください")
holidays_str = st.sidebar.text_input("休館日の日にち (例: 3,4,5,10,17)", "3,4,5,6,10,17,24,31")

holidays_list = []
if holidays_str:
    try:
        holidays_list = [int(x.strip()) for x in holidays_str.split(',') if x.strip()]
    except:
        st.sidebar.error("数字とカンマだけで入力してください")

# --- メイン画面 ---
st.markdown("""
### 1. 契約条件データのアップロード
個人名に依存しない汎用的な `契約条件.csv` をアップロードしてください。
必要な列: `従業員名`, `シフトコード`, `職位/役割`, `土曜出勤上限`, `週勤務上限`, `夜勤コアチームフラグ`, `日勤専従フラグ`, `柔軟シフトフラグ`
""")

uploaded_file = st.file_uploader("CSVファイルを選択", type=['csv'])

if uploaded_file is not None:
    try:
        # Shift-JISでトライ、ダメならUTF-8
        try:
            contracts_df = pd.read_csv(uploaded_file, encoding='shift_jis')
        except:
            contracts_df = pd.read_csv(uploaded_file, encoding='utf-8')
            
        st.success("CSVの読み込みに成功しました！以下のデータでシフトを作成します。")
        st.dataframe(contracts_df.head())
        
        if st.button("✨ シフトを自動生成する", type="primary"):
            with st.spinner("最適化エンジンがシフトを計算中です... (数秒〜数十秒かかります)"):
                success, df_output, output_filename = solve_shift(contracts_df, year, month, holidays_list)
                
                if success:
                    st.success("✅ シフトの生成に成功しました！")
                    
                    # 結果のプレビュー
                    st.write(f"### {year}年{month}月 シフト表プレビュー")
                    st.dataframe(df_output)
                    
                    # Excelとしてメモリ上に保存
                    output = BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df_output.to_excel(writer, sheet_name='シフト表')
                    excel_data = output.getvalue()
                    
                    st.download_button(
                        label="📥 Excelファイルをダウンロード",
                        data=excel_data,
                        file_name=output_filename,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.error("❌ 条件が厳しすぎて解が見つかりませんでした。制約（休日数や出勤上限）を緩和して再度お試しください。")
                    
    except Exception as e:
        st.error(f"ファイルの読み込み中にエラーが発生しました: {e}")
