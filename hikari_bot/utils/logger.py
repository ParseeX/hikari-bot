import os
import re
from datetime import datetime
from hikari_bot.utils.constants import DATA_DIR

log_file = None

def get_bot_startup_info():
    """从日志文件名中提取启动时间并计算运行时长"""
    if not log_file:
        return "未知", "未知"
    
    try:
        filename = os.path.basename(log_file)
        match = re.search(r'bot_log_(\d{8})_(\d{6})\.log', filename)
        
        if match:
            date_str, time_str = match.groups()
            startup_time = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
            
            # 计算运行时长
            current_time = datetime.now()
            uptime = current_time - startup_time
            
            # 格式化启动时间
            startup_str = startup_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # 格式化运行时长
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            if days > 0:
                uptime_str = f"{days}天{hours}小时{minutes}分钟"
            elif hours > 0:
                uptime_str = f"{hours}小时{minutes}分钟"
            else:
                uptime_str = f"{minutes}分钟{seconds}秒"
            
            return startup_str, uptime_str
        else:
            return "解析失败", "未知"
    except Exception as e:
        return f"获取失败: {e}", "未知"

def new_log_file():
    global log_file
    log_file = os.path.join(DATA_DIR, f"bot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

async def log_message(message: str):
    if log_file:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")

async def log_read():
    if log_file and os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            return f.read()
    return "日志文件不存在。"