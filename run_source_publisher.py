"""由固定发布器服务运行；工作目录不随用户源码切换。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hikari_bot.qq_official.source_publisher import Publisher

if __name__ == '__main__':
    Publisher().serve()
