"""集换社监测通知的状态机；与 QQ 和手机连接无关。"""
import json
import logging
import time
from pathlib import Path


class HealthNotifications:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.state = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(self.state, dict):
                raise ValueError('invalid state')
        except FileNotFoundError:
            self.state = {}
        except (ValueError, OSError):
            logging.warning('jihuanshe monitor state could not be read')
            self.state = {}

    async def observe(self, health, send, *, now=None):
        """投递成功才确认通知；失败会在下轮重试。"""
        now = time.time() if now is None else now
        state = dict(self.state)
        messages = []
        version = health.get('version') if health else None
        ready = bool(health and health.get('ok') is True
                     and 0 <= now - health.get('checked_at', 0) <= 2400)
        if version:
            old = state.get('version')
            if old and old != version:
                result = '查价验证已通过。' if ready else '查价检查尚未通过，请留意后续故障或恢复通知。'
                messages.append(f"手机微信发生更新：{old['name']} → {version['name']}。{result}")
            state['version'] = version
        if ready:
            state['failures'] = 0
            if state.get('alerted'):
                messages.append('集换社查询已恢复，手机会话和查价验证已通过。')
            elif not state.get('initialized'):
                messages.append('集换社监测已启用，当前查价验证通过。微信更新、持续故障和恢复时会私聊提醒你。')
            state.update(alerted=False, initialized=True)
        else:
            state['failures'] = min(2, state.get('failures', 0) + 1)
            if state['failures'] >= 2 and not state.get('alerted'):
                if not health:
                    detail = '无法连接手机桥接服务。'
                elif now - health.get('checked_at', 0) > 2400:
                    detail = '后台检查长时间没有完成。'
                elif health.get('reason') == 'price':
                    detail = '小程序会话可用，但实际查价失败。'
                else:
                    detail = '手机连接或小程序会话检查失败，自动恢复尚未成功。'
                messages.append('集换社查询异常：' + detail + '恢复后会再通知你。')
                state.update(alerted=True, initialized=True)
        if messages:
            await send('\n'.join(messages))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
        self.state = state
