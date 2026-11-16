"""已知旧功能的显式迁移状态，防止命令在新入口静默消失。"""
IDENTITY = {"绑定", "查询绑定", "绑定查询", "添加标签", "删除标签", "查看标签"}
NOTIFICATIONS = {"订阅", "退订", "切换通知", "添加账号", "删除账号", "切换门萨", "切换mensa", "门萨", "mensa"}
TOURNAMENT = {"比赛查询", "比赛签到", "签到", "退赛", "新建比赛", "比赛卡组确认", "卡组确认", "环境统计", "对阵信息", "头像压缩"}
ADMIN = {"随机一卡", "更新数据库", "读取日志", "日志", "log", "状态查询", "状态", "status", "版本查询", "版本", "version", "重载插件", "重载", "reload", "重启服务器", "reboot", "添加至白名单", "白名单", "清空白名单", "广播", "成员列表", "开机", "关机", "jm", "MyCard采集状态", "MyCard文件", "MyCard规则检查", "卡组查询", "重置卡价数据库", "cardrush_test", "切换AI权限", "切换本地模型权限", "卡价图报", "开启解析", "关闭解析"}
PRICES = {"卡价曲线", "历史卡价", "卡价历史", "卡价日报", "卡价日报网页", "卡价日报HTML"}


def unavailable_reply(command, text):
    if command in IDENTITY:
        return "旧 QQ 身份尚未与官方 OpenID 核验关联，此功能暂未迁移；没有读取或覆盖你的旧绑定、标签。查询 MyCard 时可直接输入用户名。"
    if command in NOTIFICATIONS:
        return "官方入口尚未启用通知投递。旧订阅没有导入，未核验关联的旧目标不会发送；本次未新增或取消任何订阅。"
    if command in ADMIN:
        return "此管理功能尚未在官方入口授权或启用。旧 QQ 管理员权限不会自动继承。"
    if command in TOURNAMENT:
        return "比赛相关功能按管理员要求暂缓迁移；未执行报名、签到、退赛或写入比赛数据。"
    if command in PRICES:
        return "该卡价历史/日报功能尚未完成官方入口迁移；卡价、卡价查询和日版卡价查询可继续使用。"
    if command == "前排卡组信息":
        return "前排卡组网页版：https://ygo.xyk.one/mc\n官方入口尚未提供原合并转发外观。"
    if command in {"翻译", "bm", "ym", "blogin"} or "译" in command or "http://" in text or "https://" in text or command.startswith("BV"):
        return "该翻译或链接/视频解析功能尚未完成官方入口迁移；未下载、上传或发送预览卡片。"
    return None
