"""两个入口共用的 MyCard 统计文字和图表，不访问身份数据库。"""
import calendar
from datetime import datetime
from io import BytesIO

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import pytz

from . import mycard as mycard_service
from .mycard_history import is_zero_delta_match as _is_zero_delta_match, record_month, month_trend
from .platform import Result


async def history_report(user_id, month, year):
    current = datetime.now(pytz.timezone("Asia/Shanghai"))
    current_year, current_month = current.year, current.month
    records = await mycard_service.mycard_get_records(user_id, month, year)
    if records == None:
        return Result("查询失败，请稍后重试")

    valid_records = [record for record in records if not _is_zero_delta_match(record, user_id)]
    wins = [record for record in valid_records if record['winner'] == user_id]

    result_message = f"""玩家：{user_id}
{str(year-2000)+'年' if year!=current_year else ''}{month}月场次：{len(valid_records)}
{str(year-2000)+'年' if year!=current_year else ''}{month}月胜率：{(0 if len(valid_records)==0 else len(wins)*100.0/len(valid_records)):.2f}%"""

    if len(valid_records) > 0:
        try:
            previous_score = await mycard_service.fetch_previous_month_score(user_id, year, month)
            pt = [point['score'] for point in month_trend(valid_records, user_id, year, month, previous_score)]
        except ValueError:
            return Result('月初分数暂时无法获取，请稍后重试')

        tz_shanghai = pytz.timezone("Asia/Shanghai")
        now_shanghai = datetime.now(tz_shanghai)
        last_day = calendar.monthrange(year, month)[1]
        settlement_dt = tz_shanghai.localize(datetime(year, month, last_day, 22, 0, 0))

        is_current_month = (year == current_year and month == current_month)
        is_settled = not is_current_month or now_shanghai >= settlement_dt

        if is_settled:
            # 只统计结算时间（月末最后一天22点北京时间）前开始的对局
            settlement_records = [
                r for r in valid_records
                if datetime.strptime(r["start_time"], "%Y-%m-%dT%H:%M:%S.%fZ")
                   .replace(tzinfo=pytz.utc).astimezone(tz_shanghai) < settlement_dt
            ]
            if settlement_records:
                spt_ex = [r['pta_ex'] if r['usernamea'] == user_id else r['ptb_ex'] for r in settlement_records]
                spt = [r['pta'] if r['usernamea'] == user_id else r['ptb'] for r in settlement_records]
                spt.append(spt_ex[-1])
                spt.reverse()
                result_message = result_message + f"\n结算分数：{spt[-1]:.2f}"
            rank = await mycard_service.fetch_player_history_rank(user_id, year, month)
            if rank is not None:
                result_message = result_message + f"\n结算排名：{rank}"
        else:
            result_message = result_message + f"\n当前分数：{pt[-1]:.2f}"
            rank = await mycard_service.mycard_get_player_rank(user_id)
            if rank is not None:
                result_message = result_message + f"\n当前排名：{rank}"

        plt.figure(figsize=(8, 6))

        # 绘制折线
        plt.plot(pt, marker='.', linestyle='--', color='b', linewidth=0.5)

        # 不显示标题、标签和图例
        plt.title("")  # 不显示标题
        plt.xlabel("")  # 不显示 x 轴标签
        plt.ylabel("")  # 不显示 y 轴标签
        plt.grid(False)  # 隐藏网格

        # 标记第一个数据点和历史最高分数据点
        if pt:
            plt.text(0, pt[0], f"{pt[0]:.2f}", ha='center', va='bottom', fontsize=10, color='black')
            max_idx = pt.index(max(pt))
            plt.text(max_idx, pt[max_idx], f"{pt[max_idx]:.2f}", ha='center', va='bottom', fontsize=10, color='black')

        plt.gca().xaxis.set_major_locator(MaxNLocator(integer=True))
        plt.subplots_adjust(left=0.05, right=0.95, top=0.95, bottom=0.05)

        # 保存图表到内存中的二进制流
        buf = BytesIO()
        plt.savefig(buf, format="png", bbox_inches='tight')
        buf.seek(0)  # 将文件指针移到开始位置
        plt.close()

        return Result(result_message, (buf.getvalue(),))

    return Result(result_message)


async def winrate_report(user_id):
    records = await mycard_service.fetch_player_history(user_id)
    if records == None:
        return Result("查询失败，请稍后重试")

    if len(records) == 0:
        return Result(f"玩家 {user_id} 暂无对战记录")

    valid_records = [record for record in records if not _is_zero_delta_match(record, user_id)]

    # 按月份分组记录
    monthly_data = {}
    for record in valid_records:
        month_key = record_month(record)
        if month_key not in monthly_data:
            monthly_data[month_key] = {'total': 0, 'wins': 0}

        monthly_data[month_key]['total'] += 1
        if record['winner'] == user_id:
            monthly_data[month_key]['wins'] += 1

    # 排序月份并计算胜率
    sorted_months = sorted(monthly_data.keys())
    win_rates = []
    month_labels = []

    for month_key in sorted_months:
        total_games = monthly_data[month_key]['total']
        wins = monthly_data[month_key]['wins']
        win_rate = (wins * 100.0 / total_games) if total_games > 0 else 0.0

        win_rates.append(win_rate)
        # 格式化月份标签
        year, month = month_key.split('-')
        month_labels.append(f"{year[-2:]}/{int(month)}")

    total_wins = len([record for record in valid_records if record['winner'] == user_id])
    total_games = len(valid_records)
    overall_rate = (total_wins * 100.0 / total_games) if total_games > 0 else 0.0

    result_message = f"""玩家：{user_id}
总场次：{total_games}
总胜率：{overall_rate:.2f}%
有对局的月份：{len(sorted_months)}个"""

    # 生成胜率曲线图
    if len(win_rates) > 0:
        plt.figure(figsize=(12, 6))

        # 绘制胜率曲线
        plt.plot(range(len(win_rates)), win_rates, marker='o', linestyle='-', color='b', linewidth=2, markersize=6)

        # 设置图表
        plt.ylim(0, 100)  # 胜率范围0-100%
        #plt.ylabel("胜率 (%)", fontsize=12)
        #plt.xlabel("月份", fontsize=12)
        #plt.title(f"{user_id} 月度胜率统计", fontsize=14, fontweight='bold')
        plt.grid(True, alpha=0.3)

        # 设置x轴标签
        plt.xticks(range(len(month_labels)), month_labels, rotation=45, ha='right')

        # 在数据点上显示胜率值
        for i, rate in enumerate(win_rates):
            plt.text(i, rate + 2, f"{rate:.1f}%", ha='center', va='bottom', fontsize=9)

        # 添加平均线
        avg_rate = sum(win_rates) / len(win_rates)
        plt.axhline(y=avg_rate, color='r', linestyle='--', alpha=0.7, label=f'avg: {avg_rate:.1f}%')
        plt.legend()

        plt.tight_layout()

        # 保存图表到内存中的二进制流
        buf = BytesIO()
        plt.savefig(buf, format="png", bbox_inches='tight', dpi=150)
        buf.seek(0)  # 将文件指针移到开始位置
        plt.close()

        return Result(result_message, (buf.getvalue(),))

    return Result(result_message)
