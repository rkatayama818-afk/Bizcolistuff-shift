import pandas as pd
import datetime
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

    # シフト定義
    night_shifts = ['L', 'D', 'Ｄ', 'E', 'H', 'Ｈ'] 
    day_shifts = ['N', 'S', 'A', 'B', 'Ｂ', 'C', 'Ｃ', 'F', 'Ｆ', 'G', 'Ｇ']
    all_shift_types = list(set(day_shifts + night_shifts))

    weekday_list = [(start_date + datetime.timedelta(days=d-1)).weekday() for d in range(1, days_in_month + 1)]

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
    for e_idx, row in contracts_df.iterrows():
        sat_work_vars = [sum(shifts[(e_idx, d, s)] for s in all_shift_types) for d in saturdays]
        
        # R-13: 土曜日の2週連続勤務禁止（全スタッフ対象）
        for i in range(len(saturdays) - 1):
            model.Add(sat_work_vars[i] + sat_work_vars[i+1] <= 1)
        
        # 土曜出勤の合計回数
        sat_count = sum(sat_work_vars)
        
        # 土曜出勤上限の適用 (CSVの列データを使用)
        max_sat = int(row.get('土曜出勤上限', 0))
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
        contract_shift = str(row.get('シフトコード', '')).strip()
        is_night_core = int(row.get('夜勤コアチームフラグ', 0)) == 1
        is_flexible = int(row.get('柔軟シフトフラグ', 0)) == 1
        
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
                if not is_flexible:
                    # 柔軟シフトフラグが0の人は、自分のシフトコード以外は入れない
                    if s != contract_shift:
                        model.Add(shifts[(e_idx, d, s)] == 0)
                else:
                    # 柔軟シフトフラグが1の人も、平日は基本枠に従う（ただし自身のグループ内なら柔軟）
                    if not is_sat_day and s != contract_shift:
                        # 夜勤コアなら夜勤枠の他のシフトには入れる
                        if is_night_core and (s not in night_shifts):
                            model.Add(shifts[(e_idx, d, s)] == 0)
                        elif not is_night_core and (s not in day_shifts):
                            model.Add(shifts[(e_idx, d, s)] == 0)

        # 勤務日数上限（週単位）
        lim = int(row.get('週勤務上限', 5))
        for w in weeks:
            model.Add(sum(shifts[(e_idx, d, s)] for d in w for s in all_shift_types) <= lim)

    # --- D. 禁止ルール ---
    for e in employees:
        for d in range(1, days_in_month):
            # L翌日の制限 (A, N を禁止)
            model.Add(shifts[(e, d+1, 'A')] == 0).OnlyEnforceIf(shifts[(e, d, 'L')])
            model.Add(shifts[(e, d+1, 'N')] == 0).OnlyEnforceIf(shifts[(e, d, 'L')])
        # 5連勤禁止
        for d in range(1, days_in_month - 4):
            model.Add(sum(sum(shifts[(e, d + i, s)] for s in all_shift_types) for i in range(6)) <= 5)

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
    # ==========================================
    # 日勤リソースの最大化
    model.Maximize(sum(shifts[(e, d, s)] for e in employees for d in days for s in all_shift_types))

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
