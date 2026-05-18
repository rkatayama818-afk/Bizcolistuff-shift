import pandas as pd
import datetime
import unicodedata
from ortools.sat.python import cp_model

def solve_shift(contracts_df, year, month, holidays_list, staff_requests=None):
    if staff_requests is None:
        staff_requests = {}
        
    # カレンダー設定
    start_date = datetime.date(year, month, 1)
    
    # 次の月の1日から1日引いて月末日を求める
    if month == 12:
        next_month = datetime.date(year + 1, 1, 1)
    else:
        next_month = datetime.date(year, month + 1, 1)
    days_in_month = (next_month - datetime.timedelta(days=1)).day

    holidays = holidays_list

    # シフト定義（表記揺れを防ぐため、すべて半角大文字で統一）
    night_shifts = ['L', 'D', 'E', 'H'] 
    day_shifts = ['N', 'S', 'A', 'B', 'C', 'F', 'G']
    all_shift_types = day_shifts + night_shifts
    # 「金曜遅番」として扱うコード（L が遅番＝夜勤コード）
    late_shift_codes = ['L']

    weekday_list = [(start_date + datetime.timedelta(days=d-1)).weekday() for d in range(1, days_in_month + 1)]

    # ==========================================
    # データの前処理（安全な数値化）
    # ==========================================
    def safe_numeric(df, col, default=0):
        if col not in df.columns:
            df[col] = default
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(default)

    safe_numeric(contracts_df, '土曜出勤上限', 0)
    safe_numeric(contracts_df, '週勤務上限', 5)
    safe_numeric(contracts_df, '夜勤コアチームフラグ', 0)
    safe_numeric(contracts_df, '日勤専従フラグ', 0)
    safe_numeric(contracts_df, '柔軟シフトフラグ', 0)

    # ==========================================
    # 特定スタッフのインデックスを名前で取得するヘルパー
    # ==========================================
    def get_emp_idx(name_keyword):
        """従業員名に name_keyword を含む行のインデックスを返す（見つからなければ None）"""
        for idx, row in contracts_df.iterrows():
            if name_keyword in str(row.get('従業員名', '')):
                return idx
        return None

    idx_kamata   = get_emp_idx('鎌田')
    idx_nakamine = get_emp_idx('仲嶺')
    idx_mizumoto = get_emp_idx('水元')
    idx_kamimura = get_emp_idx('上村')
    idx_kondo    = get_emp_idx('近藤')
    idx_taniguchi= get_emp_idx('谷口')

    # ==========================================
    # 数理モデルの構築
    # ==========================================
    model = cp_model.CpModel()
    num_employees = len(contracts_df)
    employees = range(num_employees)
    days = range(1, days_in_month + 1)

    shifts = {}
    for e in employees:
        for d in days:
            for s in all_shift_types:
                shifts[(e, d, s)] = model.NewBoolVar(f'shift_e{e}_d{d}_s{s}')

    # ==========================================
    # 制約条件の追加
    # ==========================================

    # --- A. 基本的な人員充足制約 ---
    for d in days:
        is_holiday = d in holidays
        is_sat = weekday_list[d-1] == 5

        if is_holiday:
            for e in employees:
                for s in all_shift_types: model.Add(shifts[(e, d, s)] == 0)
        else:
            for e in employees:
                model.Add(sum(shifts[(e, d, s)] for s in all_shift_types) <= 1)

            if is_sat:
                # 土曜：日勤2名、夜勤0名
                model.Add(sum(shifts[(e, d, s)] for e in employees for s in day_shifts) == 2)
                model.Add(sum(shifts[(e, d, s)] for e in employees for s in night_shifts) == 0)
            else:
                # 平日：夜勤2名、日勤最大化（最低6名確保）
                model.Add(sum(shifts[(e, d, s)] for e in employees for s in night_shifts) == 2)
                model.Add(sum(shifts[(e, d, s)] for e in employees for s in day_shifts) >= 6)

    # --- B. 土曜日の個別制限と公平性 ---
    saturdays = [d for d in days if weekday_list[d-1] == 5]
    num_saturdays = len(saturdays)

    for e_idx, row in contracts_df.iterrows():
        sat_work_vars = [sum(shifts[(e_idx, d, s)] for s in all_shift_types) for d in saturdays]
        
        # R-13: 土曜日の2週連続勤務禁止（全スタッフ対象）
        for i in range(len(saturdays) - 1):
            model.Add(sat_work_vars[i] + sat_work_vars[i+1] <= 1)
        
        # 土曜出勤の合計回数
        sat_count = sum(sat_work_vars)
        
        # 土曜出勤上限の適用 (CSVの列データを使用)
        # ★ 仲嶺さんは5週の月だけ最大3回に引き上げ
        if e_idx == idx_nakamine and num_saturdays >= 5:
            model.Add(sat_count <= 3)
        else:
            max_sat = int(row['土曜出勤上限'])
            model.Add(sat_count <= max_sat)

    # --- C. チーム分けと上限設定 ---
    weeks = []
    temp_w = []
    for d in days:
        temp_w.append(d)
        if weekday_list[d-1] == 6:
            weeks.append(temp_w); temp_w = []
    if temp_w: weeks.append(temp_w)

    for e_idx, row in contracts_df.iterrows():
        contract_shift_raw = str(row.get('シフトコード', '')).strip()
        contract_shift = unicodedata.normalize('NFKC', contract_shift_raw).upper()
        
        is_night_core = int(row['夜勤コアチームフラグ']) == 1
        is_flexible = int(row['柔軟シフトフラグ']) == 1
        
        for d in days:
            is_sat_day = weekday_list[d-1] == 5
            if not is_sat_day: # 平日
                if is_night_core:
                    # 夜勤コアチームは平日日勤禁止
                    for s in day_shifts: model.Add(shifts[(e_idx, d, s)] == 0)
                else:
                    # 日勤チームは平日夜勤禁止
                    for s in night_shifts: model.Add(shifts[(e_idx, d, s)] == 0)
            
            # 契約コード以外の割り当て制限
            for s in all_shift_types:
                if contract_shift not in all_shift_types:
                    # 【安全装置】無効なコード（空欄など）の場合は、自分のグループ内で柔軟に入れるようにする
                    if not is_sat_day:
                        if is_night_core and (s not in night_shifts):
                            model.Add(shifts[(e_idx, d, s)] == 0)
                        elif not is_night_core and (s not in day_shifts):
                            model.Add(shifts[(e_idx, d, s)] == 0)
                elif int(row['日勤専従フラグ']) == 1:
                    # 旧コードの「波多さんは日勤(A)のみ」の汎用化：常に自分のシフトコード固定
                    if s != contract_shift:
                        model.Add(shifts[(e_idx, d, s)] == 0)
                elif not is_flexible:
                    # 柔軟シフトフラグが0の人：平日のみ自分のシフトコードに固定（土曜は柔軟に日勤に入れる）
                    if not is_sat_day and s != contract_shift:
                        model.Add(shifts[(e_idx, d, s)] == 0)
                else:
                    # 柔軟シフトフラグが1の人：平日は自身のグループ(日/夜)内なら柔軟、土曜も柔軟
                    if not is_sat_day and s != contract_shift:
                        if is_night_core and (s not in night_shifts):
                            model.Add(shifts[(e_idx, d, s)] == 0)
                        elif not is_night_core and (s not in day_shifts):
                            model.Add(shifts[(e_idx, d, s)] == 0)

        # 勤務日数上限（週単位）
        lim = int(row['週勤務上限'])
        for w in weeks:
            model.Add(sum(shifts[(e_idx, d, s)] for d in w for s in all_shift_types) <= lim)

    # --- D. 禁止ルール（既存） ---
    for e in employees:
        for d in range(1, days_in_month):
            # L翌日の制限 (A, N を禁止)
            model.Add(shifts[(e, d+1, 'A')] == 0).OnlyEnforceIf(shifts[(e, d, 'L')])
            model.Add(shifts[(e, d+1, 'N')] == 0).OnlyEnforceIf(shifts[(e, d, 'L')])
        # 5連勤禁止
        for d in range(1, days_in_month - 4):
            model.Add(sum(sum(shifts[(e, d + i, s)] for s in all_shift_types) for i in range(6)) <= 5)

    # ==========================================
    # --- F. 新規ビジネスルール ---
    # ==========================================

    # F-1: 金曜遅番（Lシフト）翌日の土曜出勤禁止（全スタッフ）
    for d in range(1, days_in_month + 1):
        if weekday_list[d-1] == 4:  # 金曜
            next_d = d + 1
            if next_d <= days_in_month and weekday_list[next_d - 1] == 5:  # 翌日が土曜
                for e in employees:
                    for late_s in late_shift_codes:
                        if late_s in all_shift_types:
                            # 金曜にLを入っている場合、翌土曜は全シフト禁止
                            for s in all_shift_types:
                                model.Add(shifts[(e, next_d, s)] == 0).OnlyEnforceIf(shifts[(e, d, late_s)])

    # F-2: 鎌田さんは月曜出勤不可
    if idx_kamata is not None:
        for d in days:
            if weekday_list[d-1] == 0:  # 月曜
                for s in all_shift_types:
                    model.Add(shifts[(idx_kamata, d, s)] == 0)

    # F-3: 仲嶺さんは火曜出勤不可
    if idx_nakamine is not None:
        for d in days:
            if weekday_list[d-1] == 1:  # 火曜
                for s in all_shift_types:
                    model.Add(shifts[(idx_nakamine, d, s)] == 0)

    # F-4: 水元さんが休みの日 → 鎌田さんは必ず夜勤出勤、かつコンビは上村か仲嶺
    # 「水元さん不在」= 水元さんがその日どのシフトにも入っていない
    if idx_mizumoto is not None and idx_kamata is not None:
        for d in days:
            if d in holidays or weekday_list[d-1] == 5:
                continue  # 休館日・土曜はスキップ（夜勤がない日）
            
            # 水元が休みかどうかの変数
            mizumoto_absent = model.NewBoolVar(f'mizumoto_absent_d{d}')
            mizumoto_works = sum(shifts[(idx_mizumoto, d, s)] for s in all_shift_types)
            # mizumoto_absent = 1 ⟺ mizumoto_works == 0
            model.Add(mizumoto_works == 0).OnlyEnforceIf(mizumoto_absent)
            model.Add(mizumoto_works >= 1).OnlyEnforceIf(mizumoto_absent.Not())

            # 水元が休みのとき: 鎌田は夜勤に必ず入る
            kamata_night = sum(shifts[(idx_kamata, d, s)] for s in night_shifts)
            model.Add(kamata_night == 1).OnlyEnforceIf(mizumoto_absent)

            # 水元が休みのとき: コンビ（もう1人の夜勤）は上村か仲嶺でなければならない
            # = 上村 or 仲嶺 のどちらかが夜勤に入っている
            partners = []
            if idx_kamimura is not None:
                partners.append(sum(shifts[(idx_kamimura, d, s)] for s in night_shifts))
            if idx_nakamine is not None:
                partners.append(sum(shifts[(idx_nakamine, d, s)] for s in night_shifts))
            if partners:
                partner_sum = sum(partners)
                model.Add(partner_sum >= 1).OnlyEnforceIf(mizumoto_absent)

    # --- E. 希望休の反映 ---
    for e_idx, row in contracts_df.iterrows():
        emp_name = row['従業員名']
        req_days = staff_requests.get(emp_name, [])
        for d in req_days:
            if d in days:
                for s in all_shift_types:
                    model.Add(shifts[(e_idx, d, s)] == 0)

    # ==========================================
    # 最適化と実行
    # F-5: 土曜2回目は近藤さん優先、次に谷口さん（ソフト制約：目的関数のボーナス）
    # ==========================================
    # ベース：全シフト数の最大化
    base_obj = sum(shifts[(e, d, s)] for e in employees for d in days for s in all_shift_types)

    # 土曜のインデックス（0始まり）から「2回目の土曜」を特定
    sat_bonus_terms = []
    if len(saturdays) >= 2:
        sat2 = saturdays[1]  # 2回目の土曜
        if idx_kondo is not None:
            kondo_sat2 = sum(shifts[(idx_kondo, sat2, s)] for s in all_shift_types)
            # 近藤さんに最高優先度ボーナス（大きなウェイトを掛ける）
            sat_bonus_terms.append(kondo_sat2 * 100)
        if idx_taniguchi is not None:
            taniguchi_sat2 = sum(shifts[(idx_taniguchi, sat2, s)] for s in all_shift_types)
            # 谷口さんに次点ボーナス
            sat_bonus_terms.append(taniguchi_sat2 * 50)

    bonus_obj = sum(sat_bonus_terms) if sat_bonus_terms else 0
    model.Maximize(base_obj + bonus_obj)

    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        results = []
        for e_idx, row in contracts_df.iterrows():
            emp_name = row['従業員名']
            emp_data = {'従業員名': emp_name}
            for d in days:
                assigned = '×'
                for s in all_shift_types:
                    if solver.Value(shifts[(e_idx, d, s)]):
                        assigned = s; break
                emp_data[f'{d}日'] = assigned
            results.append(emp_data)

        # データ整形
        df_final = pd.DataFrame(results).set_index('従業員名').T
        date_info = []
        jp_weeks = ['月', '火', '水', '木', '金', '土', '日']
        for d in days:
            curr = start_date + datetime.timedelta(days=d-1)
            date_info.append({'日付': f'{d}日', '曜日': jp_weeks[curr.weekday()], '祝日': '祝' if d in holidays else ''})
        
        df_output = pd.DataFrame(date_info).set_index('日付').join(df_final)
        
        output_filename = f'シフト表_{year}年{month}月.xlsx'
        return True, df_output, output_filename
    else:
        return False, None, None
