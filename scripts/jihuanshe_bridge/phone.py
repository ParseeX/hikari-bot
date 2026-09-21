"""单台已授权 OPPO 的连接与正常微信入口恢复；不复制微信登录数据。"""
import hashlib
import io
import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent
SERVER_SHA256 = 'fa64b9207bcabe84d67b00d570d5bd7c01b8f27860263c294b7635ca5c1b8b09'
REMOTE = '/data/local/tmp/hikari-jhs-frida'


class Phone:
    def __init__(self):
        self.serial = os.environ['JHS_ADB_SERIAL']
        self.adb_binary = os.getenv('JHS_ADB', 'adb')
        self.binary = Path(os.environ['JHS_FRIDA_BINARY'])
        self.port = int(os.getenv('JHS_FRIDA_PORT', '19242'))
        self.launcher = None
        self.server_pid = None

    def adb(self, *args, check=True, binary=False):
        result = subprocess.run([self.adb_binary, '-s', self.serial, *map(str, args)],
                                capture_output=True, timeout=20)
        if check and result.returncode:
            raise RuntimeError('ADB operation failed')
        return result.stdout if binary else result.stdout.decode('utf-8', errors='replace').strip()

    def root(self, command, check=True):
        return self.adb('shell', 'su -c ' + shlex.quote(command), check=check)

    def connect(self):
        if self.adb('get-state', check=False) != 'device':
            self.adb('connect', self.serial)
        if self.adb('get-state', check=False) != 'device':
            raise RuntimeError('phone offline')

    def start_server(self):
        self.connect()
        if hashlib.sha256(self.binary.read_bytes()).hexdigest() != SERVER_SHA256:
            raise RuntimeError('unexpected frida binary')
        current = self.root('pidof hikari-jhs-frida', check=False)
        if current:
            if self.root('sha256sum ' + REMOTE).split()[0] != SERVER_SHA256:
                raise RuntimeError('unexpected running server')
            self.server_pid = int(current)
        else:
            self.adb('push', self.binary, REMOTE)
            self.adb('shell', 'chmod', '700', REMOTE)
            self.launcher = subprocess.Popen(
                [self.adb_binary, '-s', self.serial, 'shell',
                 'su -c ' + shlex.quote(REMOTE + ' -l 127.0.0.1:27042 -P')],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(30):
                current = self.root('pidof hikari-jhs-frida', check=False)
                if current.isdigit():
                    self.server_pid = int(current)
                    break
                time.sleep(.3)
            else:
                raise RuntimeError('frida server unavailable')
        self.adb('forward', '--no-rebind', f'tcp:{self.port}', 'tcp:27042', check=False)
        if not any(line.split()[1:] == [f'tcp:{self.port}', 'tcp:27042'] and line.split()[0] == self.serial
                   for line in self.adb('forward', '--list').splitlines()):
            raise RuntimeError('ADB forward unavailable')

    def locked(self):
        policy = self.adb('shell', 'dumpsys', 'window', 'policy')
        match = re.search(r'KeyguardServiceDelegate\s+showing=(\w+)', policy)
        if not match:
            raise RuntimeError('keyguard state unavailable')
        return match.group(1) == 'true'

    def mini_foreground(self):
        activities = self.adb('shell', 'dumpsys', 'activity', 'activities')
        match = re.search(r'topResumedActivity=.*? u\d+ ([^ ]+)', activities)
        return bool(match and '.plugin.appbrand.ui.AppBrandUI' in match.group(1))

    def identity(self, pid):
        """PID 和启动时钟共同避免把复用的 PID 当作原测试进程。"""
        if not pid:
            return None
        name = self.root(f'cat /proc/{int(pid)}/cmdline', check=False).split('\0')[0]
        if not re.fullmatch(r'com\.tencent\.mm:appbrand\d+', name):
            return None
        stat = self.root(f'cat /proc/{int(pid)}/stat', check=False)
        try:
            ticks = stat.rsplit(')', 1)[1].split()[19]
        except (IndexError, ValueError):
            return None
        return {'pid': int(pid), 'name': name, 'ticks': ticks}

    def kill_owned(self, identity):
        if identity and self.identity(identity['pid']) == identity:
            self.root('kill ' + str(identity['pid']))

    def unfreeze(self, pid):
        self.adb('shell', 'cmd', 'activity', 'unfreeze', '--sticky', int(pid))

    def locate_icon(self):
        raw = self.adb('exec-out', 'screencap', '-p', binary=True)
        screen = Image.open(io.BytesIO(raw)).convert('RGB')
        small = screen.resize((screen.width // 4, screen.height // 4))
        yellow = {(x, y) for y in range(small.height) for x in range(small.width)
                  if (lambda c: c[0] > 220 and c[1] > 165 and c[2] < 75)(small.getpixel((x, y)))}
        reference = Image.open(BASE / 'jhs-icon-reference.png').convert('RGB').resize((32, 32))
        candidates = []
        while yellow:
            seed = yellow.pop()
            queue, component = deque([seed]), [seed]
            while queue:
                x, y = queue.popleft()
                for point in [(x-1, y), (x+1, y)] + [(x, y+d) for d in range(-12, 13) if d]:
                    if point in yellow:
                        yellow.remove(point)
                        queue.append(point)
                        component.append(point)
            if len(component) < 300:
                continue
            x0, y0 = min(x for x, y in component)*4, min(y for x, y in component)*4
            x1, y1 = (max(x for x, y in component)+1)*4, (max(y for x, y in component)+1)*4
            if not (90 <= x1-x0 <= 200 and 90 <= y1-y0 <= 200):
                continue
            crop = screen.crop((x0, y0, x1, y1)).resize((32, 32))
            errors = [abs(a-b) for y in range(32) for x in range(32)
                      if (x-15.5)**2+(y-15.5)**2 < 13**2
                      for a, b in zip(crop.getpixel((x, y)), reference.getpixel((x, y)))]
            if sum(errors)/len(errors) < 20:
                candidates.append(((x0+x1)//2, (y0+y1)//2))
        return min(candidates, key=lambda p: p[1]) if candidates else None

    def locate_entry(self):
        path = '/data/local/tmp/hikari-jhs-ui.xml'
        try:
            self.adb('shell', 'uiautomator', 'dump', '--compressed', path)
            for node in ET.fromstring(self.adb('shell', 'cat', path)).iter('node'):
                if node.attrib.get('text') == '集换社' or node.attrib.get('content-desc') == '集换社':
                    coords = list(map(int, re.findall(r'\d+', node.attrib.get('bounds', ''))))
                    if len(coords) == 4 and coords[2] > coords[0] and coords[3] > coords[1]:
                        return (coords[0]+coords[2])//2, (coords[1]+coords[3])//2
        except (ET.ParseError, RuntimeError):
            pass
        finally:
            self.adb('shell', 'rm', '-f', path, check=False)
        return self.locate_icon()

    def open_miniapp(self):
        if self.locked():
            raise RuntimeError('device still locked')
        self.adb('shell', 'am', 'start', '-W', '-n', 'com.tencent.mm/.ui.LauncherUI')
        time.sleep(1)
        point = self.locate_entry()
        if point is None:
            self.adb('shell', 'input', 'swipe', '650', '320', '650', '1800', '500')
            time.sleep(1)
            point = self.locate_entry()
        if point is None:
            raise RuntimeError('miniapp entry not found')
        self.adb('shell', 'input', 'tap', *point)
        for _ in range(30):
            if self.mini_foreground():
                break
            time.sleep(1)
        else:
            raise RuntimeError('miniapp did not open')
        time.sleep(8)
        top = self.adb('shell', 'dumpsys', 'activity', 'top')
        matches = re.findall(r'ACTIVITY com\.tencent\.mm/\.plugin\.appbrand\.ui\.AppBrandUI\w*[^\n]*pid=(\d+)', top)
        if not matches:
            raise RuntimeError('miniapp PID unavailable')
        return int(matches[-1])

    def close(self):
        try:
            current = self.root('pidof hikari-jhs-frida', check=False)
            if current == str(self.server_pid):
                self.root('kill ' + current, check=False)
            self.adb('forward', '--remove', f'tcp:{self.port}', check=False)
            self.adb('shell', 'rm', '-f', REMOTE, check=False)
        finally:
            if self.launcher:
                self.launcher.terminate()
