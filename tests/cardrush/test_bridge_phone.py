"""冷恢复从当前 ActivityRecord 找到进程，避免依赖不完整的 activity top 输出。"""
import importlib.util
from pathlib import Path

import pytest


# 来自 Android 16 的任务记录结构，保留了同屏多个历史小程序的干扰项。
ACTIVITIES = '''
  topResumedActivity=ActivityRecord{active u0 com.tencent.mm/.plugin.appbrand.ui.AppBrandUI00 t180}
    * Hist #0: ActivityRecord{old u0 com.tencent.mm/.plugin.appbrand.ui.AppBrandUI1 t170}
      app=ProcessRecord{p0 29003:com.tencent.mm:appbrand1/u0a212}
    * Hist #0: ActivityRecord{active u0 com.tencent.mm/.plugin.appbrand.ui.AppBrandUI00 t180}
      app=ProcessRecord{p1 27843:com.tencent.mm:appbrand0/u0a212}
    * Hist #0: ActivityRecord{other u0 com.tencent.mm/.plugin.appbrand.ui.AppBrandUI2 t181}
      app=ProcessRecord{p2 29999:com.tencent.mm:appbrand2/u0a212}
'''
TOP = '''
  ACTIVITY org.lineageos.glimpse/.ViewActivity old1 pid=29250
  ACTIVITY com.google.android.gms/.nearby.sharing.main.MainActivity old2 pid=5958
'''


def load_phone(monkeypatch, activities):
    spec = importlib.util.spec_from_file_location(
        'bridge_phone_test', Path('scripts/jihuanshe_bridge/phone.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    phone = module.Phone.__new__(module.Phone)
    phone.locked = lambda: False
    phone.locate_entry = lambda: (500, 500)
    calls = []

    def adb(*args, **kwargs):
        calls.append(args)
        if args == ('shell', 'dumpsys', 'activity', 'activities'):
            return activities
        if args == ('shell', 'dumpsys', 'activity', 'top'):
            return TOP
        return ''

    phone.adb = adb
    return phone, calls


@pytest.mark.parametrize('qualified', [False, True])
def test_open_miniapp_resolves_current_task_without_activity_top(monkeypatch, qualified):
    activities = ACTIVITIES
    if qualified:
        activities = activities.replace('com.tencent.mm/.plugin',
                                        'com.tencent.mm/com.tencent.mm.plugin')
    phone, calls = load_phone(monkeypatch, activities)
    assert phone.open_miniapp() == 27843
    assert ('shell', 'dumpsys', 'activity', 'top') not in calls


@pytest.mark.parametrize('activities', [
    ACTIVITIES.replace('topResumedActivity=', 'mLastPausedActivity='),
    ACTIVITIES.replace('app=ProcessRecord{p1 27843:com.tencent.mm:appbrand0/u0a212}', 'app=null'),
    ACTIVITIES.replace('27843:com.tencent.mm:appbrand0', '27843:com.tencent.mm'),
    ACTIVITIES.replace('ActivityRecord{active u0', 'ActivityRecord{missing u0', 1),
])
def test_open_miniapp_rejects_stale_or_wrong_process(monkeypatch, activities):
    phone, _ = load_phone(monkeypatch, activities)
    with pytest.raises(RuntimeError):
        phone.open_miniapp()
