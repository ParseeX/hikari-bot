"""已知旧功能的显式迁移状态，防止命令在新入口静默消失。"""
from .output_templates import present
IDENTITY = {"绑定", "查询绑定", "绑定查询", "添加标签", "删除标签", "查看标签"}
NOTIFICATIONS = {"订阅", "退订", "切换通知", "添加账号", "删除账号", "切换门萨", "切换mensa", "门萨", "mensa"}
TOURNAMENT = {"比赛查询", "比赛签到", "签到", "退赛", "新建比赛", "比赛卡组确认", "卡组确认", "环境统计", "对阵信息", "头像压缩"}
ADMIN = {"随机一卡", "更新数据库", "读取日志", "日志", "log", "状态查询", "状态", "status", "版本查询", "版本", "version", "重载插件", "重载", "reload", "重启服务器", "reboot", "添加至白名单", "白名单", "清空白名单", "广播", "成员列表", "开机", "关机", "jm", "MyCard采集状态", "MyCard文件", "MyCard规则检查", "卡组查询", "重置卡价数据库", "cardrush_test", "切换AI权限", "切换本地模型权限", "卡价图报", "开启解析", "关闭解析"}
PRICES = {"卡价曲线", "历史卡价", "卡价历史", "卡价日报", "卡价日报网页", "卡价日报HTML"}


def unavailable_reply(command, text):
    if command in IDENTITY:
        return present('tags.availability_851cbe6a', {})
    if command in NOTIFICATIONS:
        return present('subscriptions.availability_b101c73e', {})
    if command in ADMIN:
        return present('operations.availability_7c8ef09b', {})
    if command in TOURNAMENT:
        return present('tournament.availability_5d4f9f57', {})
    if command in PRICES:
        return present('price_history.availability_4049e29c', {})
    if command == "前排卡组信息":
        return present('front_decks.availability_1ddd1afd', {})
    if command in {"翻译", "bm", "ym", "blogin"} or "译" in command or "http://" in text or "https://" in text or command.startswith("BV"):
        return present('unavailable.availability_8434d50c', {})
    return None
